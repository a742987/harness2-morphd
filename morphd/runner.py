"""The `morphd run` engine: plan, preflight, budget, upstream sweep and evidence."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import _upstream, budget, config, contract, library, pricing, runtimes, security, store


class RunError(RuntimeError):
    pass


@dataclass
class RunOptions:
    runtimes: list[str]
    k: int = 1
    modes: tuple[str, ...] = ("parallel",)
    seed: int | None = None
    workers: int = 1
    max_tasks: int | None = None
    only_domains: tuple[str, ...] = ()
    only_tasks: tuple[str, ...] = ()
    split: str = "all"
    split_file: Path | None = None
    tag: str = ""
    dry_run: bool = False
    budget_usd: dict[str, float | None] = field(default_factory=dict)
    budget_calls: dict[str, int | None] = field(default_factory=dict)
    budget_shared: bool = False
    proposer_channel: str = ""
    proposer_model: str = ""
    models: dict[str, str] = field(default_factory=dict)


def parse_modes(raw: str | list[str]) -> tuple[str, ...]:
    values = raw if isinstance(raw, list) else [raw]
    out: list[str] = []
    mapping = {
        "parallel": "agg",
        "agg": "agg",
        "sequential": "seq",
        "seq": "seq",
    }
    for value in values:
        for piece in str(value).split(","):
            piece = piece.strip().lower()
            if not piece:
                continue
            if piece not in mapping:
                raise RunError(f"unknown mode {piece!r}; expected parallel/sequential")
            mode = mapping[piece]
            if mode not in out:
                out.append(mode)
    if not out:
        out = ["agg"]
    return tuple(out)


def resolve_channel(settings: config.Settings, options: RunOptions) -> tuple[str, str]:
    channel = (
        options.proposer_channel
        or store_read_env("MORPHD_PROPOSER_CHANNEL")
        or "opencode"
    )
    model = (
        options.proposer_model
        or store_read_env("MORPHD_PROPOSER_MODEL")
        or store_read_env("HARNESS2_IMPROVER_MODEL")
    )
    if channel not in ("claude_cli", "opencode", "byok"):
        raise RunError(
            f"unknown proposer channel {channel!r}; expected claude_cli, opencode or byok"
        )
    if channel != "byok" and not options.dry_run and not model:
        raise RunError(
            "no proposer model configured; set --proposer-model / MORPHD_PROPOSER_MODEL / "
            "HARNESS2_IMPROVER_MODEL"
        )
    if model:
        if channel == "opencode" and "/" not in model:
            raise RunError(
                "opencode needs a provider-prefixed proposer model (provider/model)"
            )
        if channel == "claude_cli" and "/" in model:
            raise RunError("claude_cli needs a bare Anthropic model id, not provider/model")
    return channel, model


def store_read_env(name: str) -> str:
    import os

    return os.environ.get(name, "").strip()


def resolve_limits(raw: dict[str, str], runtimes_: list[str], kind: str) -> dict:
    out: dict = {}
    for runtime in runtimes_:
        value = raw.get(runtime)
        if value is None and len(raw) == 1 and "default" in raw:
            value = raw["default"]
        if value in (None, ""):
            out[runtime] = None
            continue
        try:
            out[runtime] = float(value) if kind == "usd" else int(value)
        except ValueError as exc:
            raise RunError(f"invalid {kind} budget for {runtime}: {value!r}") from exc
        if out[runtime] < 0:
            raise RunError(f"{kind} budget for {runtime} must be non-negative")
    return out


def _task_stream(adapter, options: RunOptions) -> dict[str, list[str]]:
    spec = _upstream.core("schedule")
    return spec.task_stream(
        adapter,
        split=options.split,
        seed=options.seed,
        only_domains=options.only_domains,
        only_tasks=options.only_tasks,
        max_tasks=options.max_tasks,
    )


def _limits_snapshot(options: RunOptions, runtime: str) -> budget.Limits:
    return budget.Limits(
        usd=options.budget_usd.get(runtime),
        calls=options.budget_calls.get(runtime),
        label=runtime,
    )


def _build_adapters(
    settings: config.Settings,
    options: RunOptions,
    channel: str,
    model: str,
    tasks_store: contract.TaskStore,
    price: pricing.Pricing,
    guards: dict[str, budget.BudgetGuard],
) -> dict[str, object]:
    out = {}
    # Upstream only accepts its own channel kinds. BYOK is injected as the actual
    # proposer object, so the adapter spec uses a legal placeholder channel.
    spec_channel = "claude_cli" if channel == "byok" else channel
    spec_model = (model or "byok-placeholder") if channel == "byok" else model
    for runtime in options.runtimes:
        rt_model = options.models.get(runtime) or settings.model_for(runtime)
        out[runtime] = runtimes.build_adapter(
            runtime,
            tasks=tasks_store,
            settings=settings,
            price=price,
            guard=guards[runtime],
            model=rt_model,
            runs_root=settings.runtime_runs_root(runtime),
            proposer_channel=spec_channel,
            proposer_model=spec_model,
        )
    return out


def _plan_rows(
    adapters: dict, tasks_store: contract.TaskStore, options: RunOptions, channel: str, price: pricing.Pricing
) -> tuple[dict[str, list[str]], list[budget.PlanRow]]:
    by_domain: dict[str, list[str]] = {}
    for runtime, adapter in adapters.items():
        by_domain = _task_stream(adapter, options)
        break
    task_refs = [task for tasks in by_domain.values() for task in tasks]
    if not task_refs:
        raise RunError(
            f"no tasks selected for split={options.split!r}; check {tasks_store.split_path()}"
        )
    counts = [tasks_store.get(task_ref).max_turns() for task_ref in task_refs]
    rows = []
    for runtime in options.runtimes:
        rows.append(
            budget.estimate_run(
                runtime=runtime,
                turn_counts=counts,
                k=options.k,
                price=price,
                limits=_limits_snapshot(options, runtime),
                proposer_channel=channel,
            )
        )
    return by_domain, rows


class GuardedProposer:
    """Wraps the upstream Proposer and charges every uncached session."""

    def __init__(self, inner, guard: budget.BudgetGuard, price: pricing.Pricing, channel: str):
        self._inner = inner
        self._guard = guard
        self._price = price
        self._channel = channel
        self.spec = inner.spec
        self.audit_dir = inner.audit_dir
        self.attempts = inner.attempts
        self.project = inner.project
        self.region = inner.region

    def call(self, *args, **kwargs):
        label = str(args[2] if len(args) > 2 else kwargs.get("label") or "session")
        self._guard.reserve(kind="session", label=label)
        try:
            reply = self._inner.call(*args, **kwargs)
        except Exception as exc:
            cost = getattr(exc, "cost_usd", 0.0) or 0.0
            estimated = cost <= 0
            if estimated:
                cost = self._price.estimate_session_usd(self._channel)
            self._guard.charge(
                cost, kind="session", label=label, estimated=estimated
            )
            raise
        if getattr(reply, "cached", False):
            self._guard.release(kind="session", label=label)
            return reply
        cost = getattr(reply, "cost_usd", 0.0) or 0.0
        estimated = cost <= 0
        if estimated:
            cost = self._price.estimate_session_usd(self._channel)
        self._guard.charge(cost, kind="session", label=label, estimated=estimated)
        return reply


def _check_proposer_ready(channel: str) -> None:
    if channel == "byok":
        from .consumer.providers import ProvidersManager
        from .consumer.service import ConsumerService
        from .consumer.settings import SettingsManager

        service = ConsumerService(
            Path(os.environ.get("MORPHD_HOME", Path.home() / ".morphd"))
        )
        settings = SettingsManager(service.store).load()
        try:
            ProvidersManager(service.store).clients_for_role(settings, "proposer")
        except Exception as exc:
            raise RunError(
                "the byok proposer needs a configured proposer provider/key "
                f"(models page): {exc}"
            ) from exc
        return
    if channel == "claude_cli":
        if shutil.which("claude") is None:
            raise RunError(
                "the claude_cli proposer needs `claude` on PATH; install Claude Code "
                "or switch to --proposer-channel opencode"
            )
        return
    proposer_mod = _upstream.core("proposer")
    cfg = proposer_mod.config
    bun = Path(str(cfg.BUN))
    opencode = Path(str(cfg.OPENCODE_DIR))
    if not bun.is_file() or not opencode.is_dir():
        raise RunError(
            "the opencode proposer needs the upstream OpenCode checkout and Bun "
            f"({bun}, {opencode}); run the upstream setup or switch to claude_cli"
        )


def _make_proposer(
    spec,
    audit_dir: Path,
    guard: budget.BudgetGuard,
    price: pricing.Pricing,
    channel: str,
    adapter=None,
):
    if channel == "byok":
        from .byok_proposer import BYOKUpstreamProposer

        return BYOKUpstreamProposer(
            spec=spec,
            adapter=adapter,
            guard=guard,
            price=price,
            audit_dir=audit_dir,
            consumer_home=Path(adapter.settings.home) if adapter is not None else None,
        )
    proposer_mod = _upstream.core("proposer")
    inner = proposer_mod.Proposer(spec, audit_dir=audit_dir)
    return GuardedProposer(inner, guard, price, channel)


def _identity_path(runs_root: Path, ns: str) -> Path:
    return runs_root / "_identity" / f"{store.safe_slug(ns)}.json"


def _check_resume_identity(runs_root: Path, ns: str, adapter, channel: str, proposer_model: str) -> None:
    current_controls = store.payload_hash(adapter.controlled_variables())
    previous = store.read_json(_identity_path(runs_root, ns))
    if not isinstance(previous, dict):
        records = sorted((runs_root / "morphd-runs").glob(f"*-{ns}.json")) if (
            runs_root / "morphd-runs"
        ).is_dir() else []
        previous = store.read_json(records[-1]) if records else None
    if not isinstance(previous, dict):
        return
    options = previous.get("options") or {}
    if previous.get("controls_hash") not in (None, current_controls):
        raise RunError(
            "the existing namespace was produced under different controlled variables "
            "(model/runtime/permission); use a new --tag or clear the namespace instead of "
            "silently reusing rollouts"
        )
    if options.get("proposer_channel") not in (None, channel) or options.get(
        "proposer_model"
    ) not in (None, proposer_model):
        raise RunError(
            "the existing namespace used a different proposer channel/model; use a new --tag"
        )


def _write_resume_identity(
    runs_root: Path, ns: str, adapter, channel: str, proposer_model: str
) -> None:
    store.atomic_write_json(
        _identity_path(runs_root, ns),
        {
            "namespace": ns,
            "runtime": adapter.runtime,
            "model": adapter.model,
            "runtime_version": adapter._runtime_version(),
            "controls_hash": store.payload_hash(adapter.controlled_variables()),
            "options": {"proposer_channel": channel, "proposer_model": proposer_model},
            "written_at": store.utc_now(),
        },
    )


def _base_tag(adapter, user_tag: str) -> str:
    tree = adapter._ht.read_tree(adapter.substrate.surface(), adapter.base_harness())
    fingerprint = store.payload_hash(tree)[:8]
    return f"{user_tag}-h{fingerprint}" if user_tag else f"h{fingerprint}"


def execute(options: RunOptions, settings: config.Settings) -> dict:
    if options.k < 1 or options.workers < 1:
        raise RunError("--k and --workers must be positive")
    if not options.runtimes:
        raise RunError("at least one runtime is required")
    modes = parse_modes(list(options.modes))
    channel, proposer_model = resolve_channel(settings, options)
    price = pricing.Pricing.load(settings.pricing_path)
    tasks_store = contract.TaskStore(settings.tasks_root, options.split_file or settings.split_file)
    # Adapters are constructed with throw-away guards for the read-only preview; these do not
    # write journals and are never used for a model call when --dry-run is set.
    preview_guards = {
        runtime: budget.BudgetGuard(limits=_limits_snapshot(options, runtime)) for runtime in options.runtimes
    }
    adapters = _build_adapters(
        settings, options, channel, proposer_model, tasks_store, price, preview_guards
    )
    by_domain, plan = _plan_rows(adapters, tasks_store, options, channel, price)
    plan_text = budget.format_plan(plan, runtime_order=options.runtimes)
    namespaces = {}
    effective_tags = {}
    schedule = _upstream.core("schedule")
    for runtime in options.runtimes:
        adapter = adapters[runtime]
        effective_tags[runtime] = _base_tag(adapter, options.tag)
        namespaces[runtime] = {
            "agg": schedule.ns(
                "md",
                adapter.substrate.name,
                "agg",
                options.k,
                seed=options.seed,
                tag=effective_tags[runtime],
            ),
            "seq": schedule.ns(
                "md",
                adapter.substrate.name,
                "seq",
                options.k,
                seed=options.seed,
                tag=effective_tags[runtime],
            ),
        }
    stream_lines = []
    stream_index = 0
    for domain, tasks in by_domain.items():
        for task in tasks:
            stream_index += 1
            stream_lines.append(f"{stream_index:>4}  {domain:<32} {task}")
    if options.dry_run:
        print(f"# morphd dry-run split={options.split} k={options.k} modes={','.join(modes)}")
        print(f"# tasks root: {tasks_store.root}")
        for runtime in options.runtimes:
            adapter = adapters[runtime]
            print(
                f"# runtime={runtime} model={options.models.get(runtime) or settings.model_for(runtime) or '<unset>'}"
            )
            print(f"#   effective tag:  {effective_tags[runtime]}")
            print(f"#   namespace[agg]: {namespaces[runtime]['agg']}")
            print(f"#   namespace[seq]: {namespaces[runtime]['seq']}")
            print(f"#   runs root:      {adapter.runs_root}")
            print(f"#   library root:   {settings.runtime_library_root(runtime)}")
        print(f"# proposer channel={channel} model={proposer_model or '<unset>'}")
        if not price.confirmed:
            print("# WARNING: pricing is confirmed:false; a real run will refuse to start")
        print("# estimated calls:")
        print(plan_text)
        print(f"# {sum(len(t) for t in by_domain.values())} tasks over {len(by_domain)} domains")
        print("# task stream:")
        print("\n".join(stream_lines))
        return {
            "dry_run": True,
            "tasks": sum(len(t) for t in by_domain.values()),
            "domains": len(by_domain),
            "plan": [row.to_dict() for row in plan],
            "namespaces": namespaces,
            "stream": by_domain,
        }

    isolation = security.require_isolation(enabled=settings.container_required)
    if not price.confirmed:
        price.require_confirmed()
    for runtime in options.runtimes:
        if options.budget_usd.get(runtime) is None:
            raise RunError(f"--budget-usd is required for {runtime}")
        if options.budget_calls.get(runtime) is None:
            raise RunError(f"--budget-calls is required for {runtime}")
    if options.budget_shared:
        total_est = sum(row.estimated_usd for row in plan)
        total_usd = sum(v or 0 for v in options.budget_usd.values())
        total_calls = sum(row.total_calls for row in plan)
        total_call_limit = sum(v or 0 for v in options.budget_calls.values())
        if total_est > total_usd:
            raise RunError(
                f"refusing to start: estimated ${total_est:.4f} exceeds the shared "
                f"--budget-usd pool ${total_usd:.2f}; no model call was made"
            )
        if total_calls > total_call_limit:
            raise RunError(
                f"refusing to start: estimated {total_calls} calls exceeds the shared "
                f"--budget-calls pool {total_call_limit}; no model call was made"
            )
    else:
        budget.check_plan(plan)
    _check_proposer_ready(channel)
    preflights = {}
    for runtime in options.runtimes:
        preflights[runtime] = adapters[runtime].preflight()
    settings.ensure_dirs()
    budget_identity = (
        f"{options.split}-k{options.k}-s{options.seed if options.seed is not None else 'none'}"
        f"-{store.payload_hash(effective_tags)[:10]}"
    )
    shared = budget.RunBudget(
        settings=settings,
        limits_by_runtime={rt: _limits_snapshot(options, rt) for rt in options.runtimes},
        shared=options.budget_shared,
        namespace=budget_identity,
        pricing_snapshot=price.to_dict(),
    )
    guards = {rt: shared.guard_for(rt) for rt in options.runtimes}
    adapters = _build_adapters(
        settings, options, channel, proposer_model, tasks_store, price, guards
    )
    results = {}
    for runtime in options.runtimes:
        adapter = adapters[runtime]
        guard = guards[runtime]
        ns = namespaces[runtime]["agg"]
        runs_root = Path(adapter.spec.runs_root) / "adapt"
        library_root = settings.runtime_library_root(runtime)
        library_root.mkdir(parents=True, exist_ok=True)
        _check_resume_identity(runs_root, ns, adapter, channel, proposer_model)
        _write_resume_identity(runs_root, ns, adapter, channel, proposer_model)
        audit_dir = runs_root / "_audit" / ns
        try:
            library.expire(
                library_root,
                runtime=runtime,
                current_fingerprint=store.payload_hash(adapter.controlled_variables()),
            )
        except Exception:
            pass
        prop = _make_proposer(
            adapter.spec.channels["proposer"], audit_dir, guard, price, channel, adapter
        )
        print(
            f"sweep {ns}: runtime={runtime} {options.workers} domain(s) parallel, "
            f"tasks in stream order",
            flush=True,
        )
        started_at = store.utc_now()
        status = "done"
        sweep = None
        error = ""
        try:
            sweep = schedule.run_sweep(
                options.k,
                adapter=adapter,
                prop=prop,
                runs_root=runs_root,
                library_root=library_root,
                split=options.split,
                modes=modes,
                seed=options.seed,
                tag=effective_tags[runtime],
                domain_parallel=options.workers,
                only_domains=options.only_domains,
                only_tasks=options.only_tasks,
                max_tasks=options.max_tasks,
                pool_policy="finals",
                selector_prop=prop if channel == "byok" else None,
            )
            if guard.exceeded:
                status = "interrupted"
            elif any(row.get("error") or row.get("failed") for row in sweep.get("domains") or []):
                status = "partial"
        except budget.BudgetExceeded as exc:
            status = "interrupted"
            error = str(exc)
        except Exception as exc:
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
        record = {
            "run_contract": 1,
            "runtime": runtime,
            "namespace": ns,
            "status": status,
            "error": error,
            "started_at": started_at,
            "finished_at": store.utc_now(),
            "options": {
                "k": options.k,
                "modes": list(modes),
                "seed": options.seed,
                "workers": options.workers,
                "max_tasks": options.max_tasks,
                "only_domains": list(options.only_domains),
                "only_tasks": list(options.only_tasks),
                "split": options.split,
                "tag": effective_tags[runtime],
                "user_tag": options.tag,
                "model": adapter.model,
                "proposer_channel": channel,
                "proposer_model": proposer_model,
            },
            "plan": [row.to_dict() for row in plan if row.runtime == runtime],
            "preflight": preflights[runtime],
            "budget": guard.snapshot(),
            "pricing": price.to_dict(),
            "isolation": isolation,
            "controls_hash": store.payload_hash(adapter.controlled_variables()),
            "sweep": sweep,
        }
        record_path = runs_root / "morphd-runs" / f"{store.utc_stamp()}-{ns}.json"
        store.atomic_write_json(record_path, record)
        try:
            stamped = library.stamp_entries(
                library_root,
                runtime=runtime,
                fingerprint=store.payload_hash(adapter.controlled_variables()),
                run_id=ns,
            )
        except Exception:
            stamped = 0
        record["record_path"] = str(record_path)
        record["library_entries_stamped"] = stamped
        store.atomic_write_json(record_path, record)
        store.atomic_write_json(
            runs_root / ns / "summary.json",
            {
                "status": status,
                "base": (record.get("sweep") or {}).get("base", []),
                "modes": (record.get("sweep") or {}).get("modes", []),
                "selections": (record.get("sweep") or {}).get("selections", {}),
                "errors": [error] if error else [],
                "morphd": {
                    "runtime": runtime,
                    "record": str(record_path),
                    "controls_hash": record["controls_hash"],
                    "budget": guard.snapshot(),
                },
            },
        )
        results[runtime] = record
        print(
            f"  -> {runtime}: {status}"
            + (f" ({error[:200]})" if error else "")
            + f"; evidence {record_path}",
            flush=True,
        )
    return {
        "dry_run": False,
        "status": "interrupted"
        if any(r["status"] == "interrupted" for r in results.values())
        else "partial"
        if any(r["status"] != "done" for r in results.values())
        else "done",
        "plan": [row.to_dict() for row in plan],
        "results": results,
    }
