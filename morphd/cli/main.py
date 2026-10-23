"""The morphd command line.

`morphd --runtime dsh ...` is the default `run` command, matching the PRD's invocation.
Every subcommand is a thin layer over a pure module so the same behaviour is testable.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
import webbrowser
from pathlib import Path

from .. import (
    __version__,
    _upstream,
    budget,
    config,
    contract,
    evaluation,
    library,
    online,
    parity,
    pricing,
    report,
    runner,
    sanitize,
    security,
    store,
    tasksource,
)
from .. import (
    promote as promotion,
)
from ..config import RUNTIMES, Settings, parse_named_values, parse_runtime_list
from ..consumer.errors import ConsumerError as _ConsumerError
from ..platform_support import require_windows
from ..runtimes.prompts import LEAK_GLOBS


def _settings(args) -> Settings:
    overrides = {}
    if getattr(args, "home", None):
        overrides["home"] = Path(args.home).expanduser().resolve()
        overrides["snapshots_root"] = overrides["home"] / "snapshots"
        overrides["promotions_root"] = overrides["home"] / "promotions"
        overrides["evaluations_root"] = overrides["home"] / "evaluations"
        overrides["libraries_root"] = overrides["home"] / "libraries"
        overrides["tasks_root"] = overrides["home"] / "tasks"
        overrides["runs_root"] = overrides["home"] / "runs"
    if getattr(args, "tasks_root", None):
        overrides["tasks_root"] = Path(args.tasks_root).expanduser().resolve()
    if getattr(args, "runs_root", None):
        overrides["runs_root"] = Path(args.runs_root).expanduser().resolve()
    settings = Settings.from_env(**overrides)
    if getattr(args, "home", None):
        candidate = Path(args.home).expanduser().resolve() / "pricing.yaml"
        if candidate.is_file():
            settings = dataclasses.replace(settings, pricing_path=candidate)
    return settings


def _models(args, runtimes_: list[str]) -> dict[str, str]:
    raw = getattr(args, "model", None) or []
    try:
        parsed = parse_named_values(raw, runtimes_[0] if len(runtimes_) == 1 else None)
    except ValueError as exc:
        raise runner.RunError(str(exc)) from exc
    return parsed


def _budget_raw(raw: list[str] | None, runtimes_: list[str]):
    parsed: dict[str, str] = {}
    values = raw or []
    for item in values:
        if "=" not in item and len(runtimes_) == 1:
            parsed[runtimes_[0]] = item
            continue
        for piece in item.split(","):
            if not piece.strip():
                continue
            if "=" not in piece:
                parsed["default"] = piece
            else:
                key, _, value = piece.partition("=")
                parsed[key.strip()] = value.strip()
    return parsed


def _dummy_guard(settings: Settings, runtime: str) -> budget.BudgetGuard:
    return budget.BudgetGuard(limits=budget.Limits(label=f"readonly-{runtime}"))


def _single_adapter(runtime: str, settings: Settings, tasks_store, price, model: str, guard=None):
    from .. import runtimes

    return runtimes.build_adapter(
        runtime,
        tasks=tasks_store,
        settings=settings,
        price=price,
        guard=guard or _dummy_guard(settings, runtime),
        model=model,
        runs_root=settings.runtime_runs_root(runtime),
        proposer_channel="claude_cli",
        proposer_model="readonly-proposer",
    )


def _print_json(payload) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, default=str))


# ---------------------------------------------------------------------------------
def cmd_run(args) -> int:
    settings = _settings(args)
    runtimes_ = parse_runtime_list(args.runtime)
    models = _models(args, runtimes_)
    budgets_usd = runner.resolve_limits(_budget_raw(args.budget_usd, runtimes_), runtimes_, "usd")
    budgets_calls = runner.resolve_limits(
        _budget_raw(args.budget_calls, runtimes_), runtimes_, "calls"
    )
    options = runner.RunOptions(
        runtimes=runtimes_,
        k=args.k,
        modes=(args.modes,),
        seed=args.seed,
        workers=args.workers,
        max_tasks=args.max_tasks,
        only_domains=tuple(s.strip() for s in args.only_domains.split(",") if s.strip()),
        only_tasks=tuple(s.strip() for s in args.only_tasks.split(",") if s.strip()),
        split=args.split,
        split_file=Path(args.split_file).expanduser().resolve() if args.split_file else None,
        tag=args.tag,
        dry_run=args.dry_run,
        budget_usd=budgets_usd,
        budget_calls=budgets_calls,
        budget_shared=args.budget_shared,
        proposer_channel=args.proposer_channel or "",
        proposer_model=args.proposer_model or "",
        models=models,
    )
    result = runner.execute(options, settings)
    if result.get("dry_run"):
        return 0
    status = result.get("status")
    return 3 if status == "interrupted" else 0 if status == "done" else 1


def cmd_evaluate(args) -> int:
    settings = _settings(args)
    runtime = args.runtime
    if runtime not in RUNTIMES:
        raise runner.RunError(f"unknown runtime {runtime!r}")
    price = pricing.Pricing.load(settings.pricing_path)
    price.require_confirmed()
    isolation = security.require_isolation(enabled=settings.container_required)
    model = args.model or settings.model_for(runtime)
    if not model:
        raise runner.RunError(
            f"no model pinned for {runtime}; set {config.MODEL_ENV[runtime]} or --model"
        )
    tasks_store = contract.TaskStore(
        settings.tasks_root,
        Path(args.split_file).expanduser().resolve() if args.split_file else settings.split_file,
    )
    splits = tuple(s.strip() for s in args.split.split(",") if s.strip())
    options = evaluation.EvaluationOptions(
        splits=splits,
        max_tasks=args.max_tasks,
        only_domains=tuple(s.strip() for s in args.only_domains.split(",") if s.strip()),
        only_tasks=tuple(s.strip() for s in args.only_tasks.split(",") if s.strip()),
    )
    selected = []
    for split in splits:
        selected.extend(tasks_store.tasks(split))
    selected = sorted(set(selected))
    if args.only_domains:
        keep = {s.strip() for s in args.only_domains.split(",") if s.strip()}
        selected = [t for t in selected if tasks_store.domain_of(t) in keep]
    if args.only_tasks:
        keep_tasks = {s.strip() for s in args.only_tasks.split(",") if s.strip()}
        selected = [t for t in selected if t in keep_tasks]
    if args.max_tasks is not None:
        selected = selected[: args.max_tasks]
    if not selected:
        raise runner.RunError("evaluation selected no tasks")
    turn_counts = [tasks_store.get(t).max_turns() for t in selected]
    limits = budget.Limits(usd=args.budget_usd, calls=args.budget_calls, label=f"eval-{runtime}")
    plan = budget.estimate_evaluation(
        runtime=runtime, turn_counts=turn_counts, price=price, limits=limits
    )
    budget.check_plan([plan])
    guard = budget.BudgetGuard(
        limits=limits,
        journal=settings.runs_root / runtime / "_budget" / f"eval-{store.utc_stamp()}.jsonl",
        pricing_snapshot=price.to_dict(),
    )
    adapter = _single_adapter(runtime, settings, tasks_store, price, model, guard)
    adapter.preflight()
    stamp = store.utc_stamp()
    report_data = evaluation.run_evaluation(
        adapter=adapter,
        tasks_store=tasks_store,
        options=options,
        candidate=Path(args.candidate).expanduser().resolve(),
        guard=guard,
        price=price,
        settings=settings,
        stamp=stamp,
    )
    report_data["isolation"] = isolation
    gate = None
    if args.gate:
        gate = evaluation.check_gate(report_data, phase=args.phase, min_tasks=args.min_tasks)
        report_data["gate"] = gate
    out = (
        Path(args.out).expanduser().resolve()
        if args.out
        else settings.evaluations_root / runtime / f"{runtime}-{stamp}.json"
    )
    store.atomic_write_json(out, report_data)
    md = out.with_suffix(".md")
    store.atomic_write_text(md, evaluation.render_markdown(report_data, gate))
    print(evaluation.render_markdown(report_data, gate))
    print(f"report: {out}")
    if gate is not None and not gate["passed"]:
        return 1
    return 3 if report_data.get("status") == "interrupted" else 0


def cmd_promote(args) -> int:
    settings = _settings(args)
    runtime = args.runtime
    if runtime not in RUNTIMES:
        raise runner.RunError(f"unknown runtime {runtime!r}")
    price = pricing.Pricing.load(settings.pricing_path)
    tasks_store = contract.TaskStore(
        settings.tasks_root,
        Path(args.split_file).expanduser().resolve() if args.split_file else settings.split_file,
    )
    model = args.model or settings.model_for(runtime)
    if not model:
        raise runner.RunError(f"no model pinned for {runtime}; set {config.MODEL_ENV[runtime]}")
    adapter = _single_adapter(runtime, settings, tasks_store, price, model)
    eval_path = Path(args.eval).expanduser().resolve()
    report_data = store.read_json(eval_path)
    if not isinstance(report_data, dict):
        raise runner.RunError(f"cannot read evaluation report {eval_path}")
    record = promotion.promote(
        adapter=adapter,
        settings=settings,
        candidate=Path(args.candidate).expanduser().resolve(),
        eval_report=report_data,
        p2=args.p2,
        p3=args.p3,
        phase=args.phase,
        min_tasks=args.min_tasks,
        target=Path(args.target).expanduser().resolve() if args.target else None,
        profile=args.profile,
    )
    print(record.get("diff", ""))
    _print_json({k: v for k, v in record.items() if k != "diff"})
    return 0


def cmd_rollback(args) -> int:
    settings = _settings(args)
    runtime = args.runtime
    if args.list:
        _print_json(promotion.list_snapshots(settings, runtime))
        return 0
    price = pricing.Pricing.load(settings.pricing_path)
    tasks_store = contract.TaskStore(settings.tasks_root, settings.split_file)
    model = args.model or settings.model_for(runtime) or "rollback-model"
    adapter = _single_adapter(runtime, settings, tasks_store, price, model)
    result = promotion.rollback(adapter, settings, args.snapshot)
    _print_json(result)
    return 0


def cmd_diff(args) -> int:
    settings = _settings(args)
    runtime = args.runtime
    price = pricing.Pricing.load(settings.pricing_path)
    tasks_store = contract.TaskStore(settings.tasks_root, settings.split_file)
    model = args.model or settings.model_for(runtime) or "diff-model"
    adapter = _single_adapter(runtime, settings, tasks_store, price, model)
    surface = adapter.substrate.surface()
    base = Path(args.base).expanduser().resolve() if args.base else adapter.base_harness()
    candidate = Path(args.candidate).expanduser().resolve()
    base_tree = adapter._ht.read_tree(surface, base)
    cand_tree = adapter._ht.read_tree(surface, candidate)
    text = promotion.render_diff(surface, base_tree, cand_tree)
    if args.out:
        store.atomic_write_text(Path(args.out).expanduser().resolve(), text)
    print(text)
    return 0


def cmd_doctor(args) -> int:
    settings = _settings(args)
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    py = sys.version_info
    add(
        "python",
        py.major == 3 and py.minor in (12, 13),
        f"{sys.version.split()[0]} (supported: 3.12/3.13)",
    )
    try:
        _upstream.load()
        add("harness2", True, "importable")
    except Exception as exc:
        add("harness2", False, str(exc))
    price = None
    try:
        price = pricing.Pricing.load(settings.pricing_path)
        add(
            "pricing",
            True,
            f"{price.path} confirmed={price.confirmed} sha256={price.digest[:12]}",
        )
        if not price.confirmed:
            add("pricing-confirmed", False, "P3 must set confirmed: true before real runs")
    except Exception as exc:
        add("pricing", False, str(exc))
    try:
        tasks_store = contract.TaskStore(settings.tasks_root, settings.split_file)
        count = len(tasks_store.all())
        add("tasks", True, f"{count} tasks at {settings.tasks_root}")
        if count:
            split_exists = tasks_store.split_path().is_file()
            add("splits", split_exists, str(tasks_store.split_path()))
            if split_exists:
                try:
                    missing = sorted(set(tasks_store.all()) - set(tasks_store.assignments()))
                    add("split-coverage", not missing, f"missing={missing[:5]}")
                except Exception as exc:
                    add("split-coverage", False, str(exc))
    except Exception as exc:
        add("tasks", False, str(exc))
        tasks_store = None
    if args.task:
        try:
            path = Path(args.task).expanduser().resolve()
            if path.is_dir():
                path = path / "contract.json"
            task = contract.TaskContract.from_path(path)
            add("task-contract", True, task.task_ref)
            workspace = path.parent / "workspace"
            actual = store.tree_hash(workspace)
            add(
                "task-workspace-hash",
                actual == task.workspace_sha256,
                f"contract={task.workspace_sha256[:16]} actual={actual[:16]}",
            )
            terms = task.hidden_terms()
            leaked = sanitize.scan_forbidden_terms(task.instruction, terms)
            problems = security.assert_no_leaks(
                terms, task.instruction, workspace, leak_globs=LEAK_GLOBS
            )
            add("task-criteria-leak", not leaked and not problems, str(leaked + problems))
            add(
                "task-runtime-agnostic",
                task.source_runtime in ("dsh", "codex", "claude-code", "manual"),
                task.source_runtime,
            )
        except Exception as exc:
            add("task-contract", False, str(exc))
    if not args.no_probe:
        for runtime in args.runtime_list:
            try:
                if price is None:
                    raise runner.RunError("pricing failed to load; runtime probe skipped")
                adapter = _single_adapter(
                    runtime,
                    settings,
                    tasks_store or contract.TaskStore(settings.tasks_root),
                    price,
                    args.model or settings.model_for(runtime),
                )
                detail = adapter.preflight()
                add(f"runtime:{runtime}", True, f"{detail.get('version')} / {detail.get('model')}")
            except Exception as exc:
                add(f"runtime:{runtime}", False, str(exc))
    target_home = settings.dsh_target_home()
    if target_home is not None:
        third_party = (target_home / "settings.yaml").exists() and any(
            str(p.name).startswith("_backup_profile_") for p in target_home.iterdir()
        ) if target_home.is_dir() else False
        add(
            "dsh-target-home",
            target_home != Path.home() / ".dsh" and not third_party,
            str(target_home),
        )
    else:
        add("dsh-target-home", True, "MORPHD_DSH_TARGET_HOME not set (required only to promote)")
    if settings.container_required:
        isolated = (
            Path("/.dockerenv").exists()
            or Path("/run/.containerenv").exists()
            or os.environ.get("MORPHD_CONTAINER_ISOLATED", "").lower() in {"1", "true"}
        )
        add("container", isolated, "real runs require the isolated container")
        add(
            "egress",
            os.environ.get("MORPHD_EGRESS_ENFORCED", "").lower() in {"1", "true"},
            "MORPHD_EGRESS_ENFORCED=1 and an allowlist are required for real runs",
        )
    failed = [c for c in checks if not c["ok"]]
    width = max(len(c["check"]) for c in checks) if checks else 10
    for check in checks:
        mark = "OK  " if check["ok"] else "FAIL"
        print(f"{mark} {check['check']:<{width}}  {check['detail']}")
    return 1 if failed else 0


def cmd_tasks(args) -> int:
    settings = _settings(args)
    if args.tasks_command == "import":
        task_store = contract.TaskStore(settings.tasks_root)
        result = tasksource.import_sessions(
            sessions_root=Path(args.sessions_root).expanduser().resolve(),
            task_store=task_store,
            domain=args.domain,
            workspace=Path(args.workspace).expanduser().resolve() if args.workspace else None,
            copy_workspace=args.copy_workspace,
            criteria_dir=Path(args.criteria_dir).expanduser().resolve() if args.criteria_dir else None,
            expected_deliverables=args.deliverable or [],
            max_sessions=args.max_sessions,
            include_all=args.include_all,
            split=not args.no_split,
            holdout=args.holdout,
            test=args.test,
            seed=args.seed,
            rules=tasksource.FailureRules(
                reasons=frozenset(
                    s.strip() for s in args.failure_reasons.split(",") if s.strip()
                ),
                min_approval_rejects=args.min_approval_rejects,
                min_tool_loops=args.min_tool_loops,
                include_corrections=not args.ignore_corrections,
            ),
        )
        _print_json(result.to_dict())
        return 0 if result.imported else 1
    task_store = contract.TaskStore(
        settings.tasks_root,
        Path(args.split_file).expanduser().resolve() if getattr(args, "split_file", None) else None,
    )
    if args.tasks_command == "list":
        split = getattr(args, "split", "all")
        items = []
        for task_ref in task_store.tasks(split):
            task = task_store.get(task_ref)
            items.append(
                {
                    "task_ref": task_ref,
                    "domain": task.domain,
                    "turns": len(task.user_turns),
                    "checks": len(task.checks),
                    "deliverables": list(task.expected_deliverables),
                    "workspace_sha256": task.workspace_sha256,
                }
            )
        if getattr(args, "json", False):
            _print_json(items)
        else:
            print(f"{'task_ref':<44} {'domain':<20} turns checks")
            for item in items:
                print(
                    f"{item['task_ref']:<44} {item['domain']:<20} {item['turns']:>5} {item['checks']:>6}"
                )
        return 0
    if args.tasks_command == "split":
        assignments = task_store.build_split_assignments(
            holdout=args.holdout, test=args.test, seed=args.seed
        )
        path = task_store.write_splits(assignments, force=args.force)
        _print_json({"split_file": str(path), "counts": _counts(assignments.values())})
        return 0
    if args.tasks_command == "validate":
        errors = []
        for task_ref in task_store.all():
            try:
                task = task_store.get(task_ref)
                task_store.verify_workspace(task_ref)
                leaked = sanitize.scan_forbidden_terms(task.instruction, task.hidden_terms())
                if leaked:
                    errors.append(f"{task_ref}: criteria terms in instruction: {leaked}")
                problems = security.assert_no_leaks(
                    task.hidden_terms(),
                    task.instruction,
                    task_store.workspace_dir(task_ref),
                    leak_globs=LEAK_GLOBS,
                )
                if problems:
                    errors.append(f"{task_ref}: " + "; ".join(problems))
            except Exception as exc:
                errors.append(f"{task_ref}: {type(exc).__name__}: {exc}")
        if errors:
            for error in errors:
                print(f"FAIL {error}")
            return 1
        print(f"OK {len(task_store.all())} tasks validated")
        return 0
    raise runner.RunError(f"unknown tasks command {args.tasks_command!r}")


def _counts(values) -> dict:
    out: dict[str, int] = {}
    for value in values:
        out[str(value)] = out.get(str(value), 0) + 1
    return dict(sorted(out.items()))


def cmd_judge(args) -> int:
    settings = _settings(args)
    runtimes_ = parse_runtime_list(args.runtime)
    price = pricing.Pricing.load(settings.pricing_path)
    tasks_store = contract.TaskStore(settings.tasks_root, settings.split_file)
    out = {}
    for runtime in runtimes_:
        model = args.model or settings.model_for(runtime) or "judge-model"
        adapter = _single_adapter(runtime, settings, tasks_store, price, model)
        out[runtime] = report.judge_runs(adapter=adapter, settings=settings, force=args.force)
    _print_json(out)
    return 0


def cmd_online(args) -> int:
    data = online.collect(Path(args.sessions_root).expanduser().resolve())
    text = online.render_markdown(data)
    if args.out:
        path = Path(args.out).expanduser().resolve()
        if args.format == "json":
            store.atomic_write_json(path, data)
        else:
            store.atomic_write_text(path, text)
        print(f"report: {path}")
    if args.format == "json":
        _print_json(data)
    else:
        print(text)
    return 0


def cmd_parity(args) -> int:
    settings = _settings(args)
    runtimes_ = parse_runtime_list(args.runtime) if args.runtime != "all" else list(RUNTIMES)
    _print_json(parity.collect(settings, runtimes_))
    return 0


def cmd_report(args) -> int:
    settings = _settings(args)
    runtimes_ = parse_runtime_list(args.runtime) if args.runtime != "all" else list(RUNTIMES)
    data = report.collect(settings, runtimes_)
    text = report.render_markdown(data)
    if args.out:
        path = Path(args.out).expanduser().resolve()
        if args.format == "json":
            store.atomic_write_json(path, data)
        else:
            store.atomic_write_text(path, text)
        print(f"report: {path}")
    if args.format == "json":
        _print_json(data)
    else:
        print(text)
    return 0


def cmd_library(args) -> int:
    settings = _settings(args)
    root = settings.runtime_library_root(args.runtime)
    if args.library_command == "stats":
        _print_json(library.stats(root))
        return 0
    fingerprint = args.fingerprint or library.fingerprint_from_runs(settings, args.runtime)
    result = library.expire(
        root, runtime=args.runtime, current_fingerprint=fingerprint, dry_run=args.dry_run
    )
    _print_json(result)
    return 0


def cmd_version(args) -> int:
    _print_json(
        {
            "morphd": __version__,
            "runtimes": config.RUNTIME_PINS,
            "harness2_commit": config.HARNESS2_COMMIT,
        }
    )
    return 0


# ---------------------------------------------------------------------------------
# Consumer commands (advanced/diagnostic entry point; the Web UI is the normal path)
# ---------------------------------------------------------------------------------
def _consumer_service(args, *, start: bool = False):
    from ..consumer.service import ConsumerService

    home = Path(args.home).expanduser().resolve() if getattr(args, "home", None) else None
    service = ConsumerService(home)
    if start:
        service.start()
    return service


def cmd_serve(args) -> int:
    from ..consumer.lifecycle import run_server

    home = Path(args.home).expanduser().resolve() if args.home else None
    if args.open:
        import threading
        import time as _time

        from ..consumer.api import LocalServer
        from ..consumer.service import ConsumerService

        service = ConsumerService(home)
        server = LocalServer(service)
        # open the browser after the server is listening
        def _open():
            _time.sleep(0.4)
            webbrowser.open(server.url)
        if not args.config_only:
            service.start()
        server.start()
        threading.Thread(target=_open, daemon=True).start()
        if not args.quiet:
            print(f"morphd UI: {server.url}", flush=True)
        try:
            while True:
                _time.sleep(0.5)
        except KeyboardInterrupt:
            return 0
        finally:
            server.stop()
            if not args.config_only:
                service.stop()
    return run_server(home, quiet=args.quiet, config_only=args.config_only)


def cmd_setup(args) -> int:
    from ..consumer.errors import ConsumerError
    from ..consumer.settings import ProjectManager

    service = _consumer_service(args)
    candidates = service.hosts_discover()
    if args.install_path:
        candidate = service.hosts.validate_manual(
            args.host,
            install_path=args.install_path,
            config_path=args.config_path,
            project_root=args.project,
        )
    else:
        compatible = [c for c in candidates if c.get("compatible") and not c.get("installed")]
        if args.host:
            compatible = [c for c in compatible if c.get("kind") == args.host]
        if not compatible:
            statuses = [
                {
                    "host": c.get("kind"),
                    "version": c.get("version"),
                    "status": c.get("status"),
                    "reason": c.get("reason"),
                    "path": c.get("executable"),
                }
                for c in candidates
            ]
            raise ConsumerError(
                "HOST_NOT_FOUND",
                "没有找到可适配的 Harness。",
                "手动选择安装目录：morphd setup --host <kind> --install-path <dir>。",
                details={"candidates": statuses},
            )
        kinds = {c["kind"] for c in compatible}
        if len(kinds) > 1 and not args.host:
            raise ConsumerError(
                "INVALID_REQUEST",
                f"发现多个 Harness：{', '.join(sorted(kinds))}。",
                "用 --host dsh|claude-code|codex 指定要适配的宿主。",
                details={"candidates": compatible},
            )
        if args.host:
            candidate = compatible[0]
        else:
            candidate = compatible[0]
    project = args.project or os.getcwd()
    if args.dry_run:
        _print_json({"dry_run": True, "candidate": candidate, "project_root": project})
        return 0
    record = service.hosts_adapt(
        {"candidate": candidate, "project_root": project, "manual": bool(args.install_path)}
    )
    project_record = ProjectManager(service.store).for_root(Path(project))
    _print_json(
        {
            "ok": True,
            "host": record,
            "project": project_record,
            "next": [
                f"重启 {record['host']}，或使用 wrapper 启动：{record.get('wrapper')}",
                "打开 Web UI：morphd serve --open",
            ],
        }
    )
    return 0


def cmd_status(args) -> int:
    service = _consumer_service(args)
    _print_json(service.status())
    return 0


def cmd_uninstall(args) -> int:
    from ..consumer.privacy import delete_scope

    service = _consumer_service(args)
    result = service.host_uninstall(args.host)
    if args.delete_data:
        result["deleted"] = delete_scope(service.store, "all")
    _print_json(result)
    return 0


def cmd_privacy(args) -> int:
    service = _consumer_service(args)
    if args.privacy_command == "export":
        _print_json(service.privacy_export())
        return 0
    if args.privacy_command == "delete":
        _print_json(
            service.privacy_delete(
                {
                    "scope": args.scope,
                    "session_id": args.session_id,
                    "project_id": args.project_id,
                }
            )
        )
        return 0
    if args.privacy_command == "retention":
        _print_json(service.privacy_retention())
        return 0
    raise ValueError("unknown privacy command")


def cmd_host(args) -> int:
    from ..consumer import lifecycle
    from ..consumer.service import ConsumerService

    home = Path(args.home).expanduser().resolve() if args.home else None
    if args.host_command == "run":
        binary = args.binary or args.kind
        return lifecycle.run_host(
            kind=args.kind,
            binary=binary,
            args=list(args.argv or []),
            home=home,
            quiet=args.quiet,
            project_root=args.project,
        )
    if args.host_command == "event":
        payload = {}
        if args.stdin:
            raw = sys.stdin.read()
            try:
                payload = json.loads(raw or "{}")
            except ValueError:
                raise ValueError("hook stdin is not valid JSON")
            if not isinstance(payload, dict):
                raise ValueError("hook stdin must be a JSON object")
        payload.update(
            {
                key: value
                for key, value in {
                    "host": args.kind,
                    "session_id": args.session_id,
                    "events_path": args.events,
                    "workspace": args.workspace,
                    "project_root": args.project,
                    "end_reason": args.end_reason,
                }.items()
                if value
            }
        )
        if args.kind == "claude-code":
            payload = lifecycle.normalize_hook_payload(args.kind, payload)
        event = args.event
        if args.spool:
            service = ConsumerService(home)
            path = service.spool_event({"type": event, "payload": payload})
            print(json.dumps({"spooled": True, "path": str(path)}))
            return 0
        delivered = lifecycle.send_internal(event, payload, home=home)
        print(json.dumps({"delivered": delivered, "event": event}))
        return 0
    raise ValueError("unknown host command")


def cmd_consumer_doctor(args) -> int:
    service = _consumer_service(args)
    _print_json(service.diagnostics())
    return 0


def cmd_tray(args) -> int:
    from ..consumer.tray import run_tray

    home = Path(args.home).expanduser().resolve() if args.home else Path(
        os.environ.get("MORPHD_HOME", Path.home() / ".morphd")
    )
    return run_tray(home)


# ---------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="morphd",
        description="Recursive harness optimisation for dsh, Codex and Claude Code.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"morphd {__version__}")
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="plan or run an optimisation sweep")
    run_p.add_argument("--runtime", default="dsh", help="dsh | codex | claude-code (comma list)")
    run_p.add_argument("--k", type=int, default=1)
    run_p.add_argument("--modes", default="parallel", help="parallel | sequential (comma list)")
    run_p.add_argument("--seed", type=int, default=None)
    run_p.add_argument("--workers", type=int, default=1)
    run_p.add_argument("--max-tasks", type=int, default=None, help="per-domain task cap")
    run_p.add_argument("--only-domains", default="")
    run_p.add_argument("--only-tasks", default="")
    run_p.add_argument("--split", choices=("all", "train", "test", "holdout"), default="all")
    run_p.add_argument("--split-file", default=None)
    run_p.add_argument("--tag", default="")
    run_p.add_argument("--dry-run", action="store_true")
    run_p.add_argument("--budget-usd", action="append", default=[], help="value or runtime=value")
    run_p.add_argument("--budget-calls", action="append", default=[], help="value or runtime=value")
    run_p.add_argument("--budget-shared", action="store_true")
    run_p.add_argument("--model", action="append", default=[], help="runtime=model (repeatable)")
    run_p.add_argument("--proposer-channel", default="", choices=("claude_cli", "opencode", ""))
    run_p.add_argument("--proposer-model", default="")
    _common_paths(run_p)
    run_p.set_defaults(func=cmd_run)

    eval_p = sub.add_parser("evaluate", help="shadow-evaluate a candidate harness")
    eval_p.add_argument("--runtime", required=True, choices=RUNTIMES)
    eval_p.add_argument("--candidate", required=True)
    eval_p.add_argument("--split", default="test,holdout")
    eval_p.add_argument("--max-tasks", type=int, default=None)
    eval_p.add_argument("--only-domains", default="")
    eval_p.add_argument("--only-tasks", default="")
    eval_p.add_argument("--budget-usd", type=float, required=True)
    eval_p.add_argument("--budget-calls", type=int, required=True)
    eval_p.add_argument("--model", default="")
    eval_p.add_argument("--out", default="")
    eval_p.add_argument("--gate", action="store_true")
    eval_p.add_argument("--phase", default="m1", choices=("m1", "m2"))
    eval_p.add_argument("--min-tasks", type=int, default=None)
    eval_p.add_argument("--split-file", default=None)
    _common_paths(eval_p)
    eval_p.set_defaults(func=cmd_evaluate)

    prom_p = sub.add_parser("promote", help="gate and apply a candidate harness")
    prom_p.add_argument("--runtime", required=True, choices=RUNTIMES)
    prom_p.add_argument("--candidate", required=True)
    prom_p.add_argument("--eval", required=True)
    prom_p.add_argument("--p2", required=True, help="P2 approver name")
    prom_p.add_argument("--p3", required=True, help="P3 approver name")
    prom_p.add_argument("--phase", default="m1", choices=("m1", "m2"))
    prom_p.add_argument("--min-tasks", type=int, default=None)
    prom_p.add_argument("--target", default=None)
    prom_p.add_argument("--profile", default=promotion.PROMOTED_PROFILE_DEFAULT)
    prom_p.add_argument("--model", default="")
    prom_p.add_argument("--split-file", default=None)
    _common_paths(prom_p)
    prom_p.set_defaults(func=cmd_promote)

    roll_p = sub.add_parser("rollback", help="restore a promotion snapshot")
    roll_p.add_argument("--runtime", required=True, choices=RUNTIMES)
    roll_p.add_argument("--snapshot", default=None)
    roll_p.add_argument("--list", action="store_true")
    roll_p.add_argument("--model", default="")
    _common_paths(roll_p)
    roll_p.set_defaults(func=cmd_rollback)

    diff_p = sub.add_parser("diff", help="render a candidate harness diff")
    diff_p.add_argument("--runtime", required=True, choices=RUNTIMES)
    diff_p.add_argument("--candidate", required=True)
    diff_p.add_argument("--base", default=None)
    diff_p.add_argument("--out", default=None)
    diff_p.add_argument("--model", default="")
    _common_paths(diff_p)
    diff_p.set_defaults(func=cmd_diff)

    doc = sub.add_parser("doctor", help="offline health checks")
    doc.add_argument("--task", default=None)
    doc.add_argument("--runtime", default="dsh", help="comma list to probe")
    doc.add_argument("--model", default="")
    doc.add_argument("--no-probe", action="store_true")
    _common_paths(doc)
    doc.set_defaults(func=cmd_doctor, runtime_list=[])

    tasks_p = sub.add_parser("tasks", help="task source operations")
    tsub = tasks_p.add_subparsers(dest="tasks_command", required=True)
    imp = tsub.add_parser("import", help="import failed dsh sessions")
    imp.add_argument("--sessions-root", required=True)
    imp.add_argument("--domain", required=True)
    imp.add_argument("--workspace", default=None)
    imp.add_argument("--copy-workspace", action="store_true")
    imp.add_argument("--criteria-dir", default=None)
    imp.add_argument("--deliverable", action="append", default=[])
    imp.add_argument("--max-sessions", type=int, default=None)
    imp.add_argument("--include-all", action="store_true")
    imp.add_argument("--no-split", action="store_true")
    imp.add_argument("--holdout", type=float, default=0.2)
    imp.add_argument("--test", type=float, default=0.5)
    imp.add_argument("--seed", type=int, default=0)
    imp.add_argument("--failure-reasons", default="error,blocked,max-tokens,aborted")
    imp.add_argument("--min-approval-rejects", type=int, default=3)
    imp.add_argument("--min-tool-loops", type=int, default=1)
    imp.add_argument("--ignore-corrections", action="store_true")
    _common_paths(imp)
    imp.set_defaults(func=cmd_tasks)
    lst = tsub.add_parser("list", help="list tasks")
    lst.add_argument("--split", choices=("all", *contract.SPLIT_NAMES), default="all")
    lst.add_argument("--json", action="store_true")
    lst.add_argument("--split-file", default=None)
    _common_paths(lst)
    lst.set_defaults(func=cmd_tasks)
    spl = tsub.add_parser("split", help="create the immutable split file")
    spl.add_argument("--holdout", type=float, default=0.2)
    spl.add_argument("--test", type=float, default=0.5)
    spl.add_argument("--seed", type=int, default=0)
    spl.add_argument("--force", action="store_true")
    _common_paths(spl)
    spl.set_defaults(func=cmd_tasks)
    val = tsub.add_parser("validate", help="validate contracts and workspace hashes")
    _common_paths(val)
    val.set_defaults(func=cmd_tasks)

    judge_p = sub.add_parser("judge", help="score selected rollouts")
    judge_p.add_argument("--runtime", default="dsh")
    judge_p.add_argument("--force", action="store_true")
    judge_p.add_argument("--model", default="")
    _common_paths(judge_p)
    judge_p.set_defaults(func=cmd_judge)

    online_p = sub.add_parser("online-report", help="online-arm trends from dsh sessions")
    online_p.add_argument("--sessions-root", required=True)
    online_p.add_argument("--format", choices=("markdown", "json"), default="markdown")
    online_p.add_argument("--out", default=None)
    _common_paths(online_p)
    online_p.set_defaults(func=cmd_online)

    parity_p = sub.add_parser("parity", help="runtime parity diagnostics")
    parity_p.add_argument("--runtime", default="all")
    _common_paths(parity_p)
    parity_p.set_defaults(func=cmd_parity)

    report_p = sub.add_parser("report", help="per-runtime report")
    report_p.add_argument("--runtime", default="all")
    report_p.add_argument("--format", choices=("markdown", "json"), default="markdown")
    report_p.add_argument("--out", default=None)
    _common_paths(report_p)
    report_p.set_defaults(func=cmd_report)

    lib_p = sub.add_parser("library", help="experience-library metadata")
    lsub = lib_p.add_subparsers(dest="library_command", required=True)
    gc = lsub.add_parser("gc", help="quarantine entries from a different fingerprint")
    gc.add_argument("--runtime", required=True, choices=RUNTIMES)
    gc.add_argument("--fingerprint", default="")
    gc.add_argument("--dry-run", action="store_true")
    _common_paths(gc)
    gc.set_defaults(func=cmd_library)
    stats = lsub.add_parser("stats", help="count entries")
    stats.add_argument("--runtime", required=True, choices=RUNTIMES)
    _common_paths(stats)
    stats.set_defaults(func=cmd_library)

    serve_p = sub.add_parser("serve", help="run the local Web UI (advanced/diagnostic)")
    serve_p.add_argument("--open", action="store_true", help="open the UI in the default browser")
    serve_p.add_argument("--quiet", action="store_true")
    serve_p.add_argument("--config-only", action="store_true", help="no worker, no automatic learning")
    serve_p.add_argument("--home", default=None)
    serve_p.set_defaults(func=cmd_serve)

    setup_p = sub.add_parser("setup", help="discover and adapt a local Harness")
    setup_p.add_argument("--host", choices=RUNTIMES, default="")
    setup_p.add_argument("--project", default=None)
    setup_p.add_argument("--install-path", default=None)
    setup_p.add_argument("--config-path", default=None)
    setup_p.add_argument("--dry-run", action="store_true")
    setup_p.add_argument("--home", default=None)
    setup_p.set_defaults(func=cmd_setup)

    status_p = sub.add_parser("status", help="consumer status as JSON")
    status_p.add_argument("--home", default=None)
    status_p.set_defaults(func=cmd_status)

    uninstall_p = sub.add_parser("uninstall", help="remove a host integration")
    uninstall_p.add_argument("--host", required=True, choices=RUNTIMES)
    uninstall_p.add_argument("--delete-data", action="store_true")
    uninstall_p.add_argument("--home", default=None)
    uninstall_p.set_defaults(func=cmd_uninstall)

    privacy_p = sub.add_parser("privacy", help="export or delete local data")
    psub = privacy_p.add_subparsers(dest="privacy_command", required=True)
    pexp = psub.add_parser("export", help="write a JSON bundle without keys")
    pexp.add_argument("--home", default=None)
    pexp.set_defaults(func=cmd_privacy)
    pdel = psub.add_parser("delete", help="delete session/project/all data")
    pdel.add_argument("--scope", required=True, choices=("session", "project", "all"))
    pdel.add_argument("--session-id", default=None)
    pdel.add_argument("--project-id", default=None)
    pdel.add_argument("--home", default=None)
    pdel.set_defaults(func=cmd_privacy)
    pret = psub.add_parser("retention", help="apply the retention policy")
    pret.add_argument("--home", default=None)
    pret.set_defaults(func=cmd_privacy)

    host_p = sub.add_parser("host", help="host bridge (used by wrappers and hooks)")
    hsub = host_p.add_subparsers(dest="host_command", required=True)
    hrun = hsub.add_parser("run", help="start the UI server and launch the host")
    hrun.add_argument("--host", dest="kind", required=True, choices=RUNTIMES)
    hrun.add_argument("--binary", default="")
    hrun.add_argument("--project", default=None)
    hrun.add_argument("--quiet", action="store_true")
    hrun.add_argument("--home", default=None)
    hrun.add_argument("argv", nargs=argparse.REMAINDER)
    hrun.set_defaults(func=cmd_host)
    hevt = hsub.add_parser("event", help="send a host session event")
    hevt.add_argument("--host", dest="kind", required=True, choices=RUNTIMES)
    hevt.add_argument("--event", choices=("session-end", "session-start"), default="session-end")
    hevt.add_argument("--stdin", action="store_true", help="read a hook payload from stdin")
    hevt.add_argument("--session-id", default="")
    hevt.add_argument("--events", default=None)
    hevt.add_argument("--workspace", default=None)
    hevt.add_argument("--project", default=None)
    hevt.add_argument("--end-reason", default=None)
    hevt.add_argument("--spool", action="store_true", help="write to the spool instead of posting")
    hevt.add_argument("--home", default=None)
    hevt.set_defaults(func=cmd_host)

    cdoctor = sub.add_parser("consumer-doctor", help="consumer diagnostics without model calls")
    cdoctor.add_argument("--home", default=None)
    cdoctor.set_defaults(func=cmd_consumer_doctor)

    tray_p = sub.add_parser("tray", help="runtime tray helper (desktop NPM path)")
    tray_p.add_argument("--home", default=None)
    tray_p.set_defaults(func=cmd_tray)

    version_p = sub.add_parser("version", help="print versions")
    version_p.set_defaults(func=cmd_version)
    return parser


def _common_paths(p: argparse.ArgumentParser) -> None:
    p.add_argument("--home", default=None, help="morphd home (default ~/.morphd)")
    p.add_argument("--tasks-root", default=None)
    p.add_argument("--runs-root", default=None)


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    require_windows()
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw:
        build_parser().print_help()
        return 0
    if raw[0].startswith("-") and raw[0] not in {"-h", "--help", "--version"}:
        raw = ["run", *raw]
    parser = build_parser()
    args = parser.parse_args(raw)
    if not hasattr(args, "func"):
        parser.print_help()
        return 0
    if args.command == "doctor":
        args.runtime_list = (
            list(RUNTIMES)
            if args.runtime == "all"
            else parse_runtime_list(args.runtime)
        )
    try:
        return int(args.func(args))
    except _ConsumerError as exc:
        print(f"morphd: {exc.code}: {exc.message}", file=sys.stderr)
        if exc.action:
            print(f"  → {exc.action}", file=sys.stderr)
        return 1
    except (
        runner.RunError,
        pricing.PricingError,
        budget.BudgetError,
        contract.ContractError,
        promotion.PromotionError,
        security.SecurityError,
        evaluation.EvaluationError,
        tasksource.SessionReadError,
        _upstream.UpstreamError,
        FileNotFoundError,
        ValueError,
        RuntimeError,
    ) as exc:
        print(f"morphd: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
