"""Shared execution engine for the three runtimes.

One adapter implements the upstream BenchmarkAdapter protocol. A concrete runtime supplies
only: the substrate and spec, the harness materialisation, one model invocation, and the
runtime's completion/usage/tool extraction. Everything that must behave identically across
runtimes (workspace isolation, replay, deliverables, budgets, security, idempotency) lives
here.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import (
    _upstream,
    approvals,
    budget,
    config,
    contract,
    metrics,
    pricing,
    sanitize,
    scoring,
    security,
    store,
)
from ..contract import TaskContract, TaskStore


class RuntimeError_(RuntimeError):
    pass


def truncate_marked(value, limit: int) -> str:
    text = str(value)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"\n…[truncated {len(text) - limit} chars]"


@dataclass
class Materialised:
    runtime_home: Path
    workspace_overlay: Path
    profile_id: str = ""
    extras: dict = field(default_factory=dict)


@dataclass
class TurnResult:
    exit_code: int
    achieved: bool | None
    reason: str = ""
    bucket: str = "unavailable"
    session_id: str | None = None
    usage: dict = field(default_factory=dict)
    tool_events: list[dict] = field(default_factory=list)
    turns: int = 0
    final_text: str = ""
    stderr: str = ""
    can_continue: bool = True


@dataclass
class PreparedRun:
    runtime_home: Path
    workspace: Path
    profile_id: str = ""
    extras: dict = field(default_factory=dict)


class BaseAdapter:
    runtime = ""
    substrate_name = ""

    def __init__(
        self,
        *,
        tasks: TaskStore,
        settings: config.Settings,
        price: pricing.Pricing,
        guard: budget.BudgetGuard,
        model: str,
        runs_root: Path | None = None,
    ) -> None:
        self.tasks_store = tasks
        self.settings = settings
        self.pricing = price
        self.guard = guard
        self.model = model.strip()
        self.runs_root = Path(runs_root or settings.runtime_runs_root(self.runtime))
        self._ht = _upstream.core("tree")
        self._log = _upstream.core("runmark")
        self.spec = None
        self.substrate = None
        self._version_cache: str | None = None
        self._preflight_cache: dict | None = None
        self.criteria_terms: dict[str, list[str]] = {}

    # --- BenchmarkAdapter protocol -------------------------------------------------
    def tasks(self, split: str = "all") -> list[str]:
        return self.tasks_store.tasks(split)

    def domain_of(self, task_id: str) -> str:
        return self.tasks_store.domain_of(task_id)

    def task_prompt(self, task_id: str) -> str:
        return self.tasks_store.get(task_id).instruction

    def task_files(self, task_id: str) -> Path | None:
        return self.tasks_store.workspace_dir(task_id)

    def base_harness(self) -> Path:
        path = self.settings.base_harness(self.runtime)
        if not path.is_dir():
            raise RuntimeError_(f"base harness for {self.runtime} is missing: {path}")
        return path

    def validate_tree(self, tree: dict[str, str]) -> None:
        surface = self.substrate.surface()
        problems: list[str] = []
        for rel in sorted(tree):
            if not self._ht.is_allowed(surface, rel):
                problems.append(f"{rel}: path is outside the editable surface")
        if surface.hooks_file and surface.hooks_file in tree:
            problems.append(
                f"{surface.hooks_file}: c8 hooks are disabled in v1 and may not be edited"
            )
        if surface.plugin_dir and any(
            rel.startswith(surface.plugin_dir.rstrip("/") + "/") for rel in tree
        ):
            problems.append(f"{surface.plugin_dir}: c8 plugins are disabled in v1")
        try:
            self._ht.validate(
                surface,
                tree,
                allow_subagents=bool(self.spec.allow_subagents),
                always_on_max=surface.always_on_max,
                reserved_scripts=self.spec.reserved_scripts,
            )
        except ValueError as exc:
            problems.extend(part.strip() for part in str(exc).split("|") if part.strip())
        for rel, text in tree.items():
            body = text if isinstance(text, str) else str(text)
            if rel in surface.always_on and len(body.encode("utf-8")) > surface.always_on_max:
                problems.append(
                    f"{rel}: {len(body.encode('utf-8'))} bytes exceeds the "
                    f"{surface.always_on_max}-byte budget"
                )
            max_bytes = self._hard_always_on_bytes()
            if rel in surface.always_on and max_bytes and len(body.encode("utf-8")) > max_bytes:
                problems.append(
                    f"{rel}: {len(body.encode('utf-8'))} bytes exceeds the runtime's hard "
                    f"{max_bytes}-byte truncation limit"
                )
        try:
            base_tree = self._ht.read_tree(self.substrate.surface(), self.base_harness())
        except Exception:
            base_tree = {}
        scanned = security.scan_tree_contents(tree, base_tree=base_tree)
        problems.extend(scanned.violations)
        for finding in scanned.findings:
            problems.append(
                f"{finding.path}:{finding.line}: secret-shaped content ({finding.kind})"
            )
        if problems:
            raise ValueError(" | ".join(dict.fromkeys(problems)))

    def _hard_always_on_bytes(self) -> int:
        return 0

    def execute(
        self, task_id: str, harness_dir: Path | None, run_id: str, *, timeout: int | None = None
    ) -> Path:
        task = self.tasks_store.get(task_id)
        source_tree = Path(harness_dir) if harness_dir is not None else self.base_harness()
        rejected: list[str] = []
        tree = self._ht.read_tree(self.substrate.surface(), source_tree, rejected=rejected)
        if rejected:
            raise ValueError(f"harness tree has off-surface files: {sorted(rejected)}")
        self.validate_tree(tree)
        harness_hash = store.payload_hash(tree)
        task_hash = task.content_hash()
        task_dir = self.runs_root / "rollouts" / store.safe_slug(task_id)
        run_dir = task_dir / store.safe_slug(run_id, max_len=110)
        marker = run_dir / ".morphd-complete"
        lock_path = run_dir.with_name(run_dir.name + ".lock")
        with store.file_lock(lock_path):
            existing = store.read_json(marker)
            if (
                isinstance(existing, dict)
                and existing.get("run_id") == run_id
                and existing.get("harness_hash") == harness_hash
                and existing.get("task_hash") == task_hash
                and existing.get("runtime") == self.runtime
                and existing.get("model") == self.model
                and existing.get("runtime_version") == self._runtime_version()
            ):
                return run_dir
            if run_dir.exists():
                shutil.rmtree(run_dir)
            run_dir.mkdir(parents=True)
            meta = self._base_meta(task, run_id, harness_dir, harness_hash, task_hash)
            self._write_meta(run_dir, meta)
            try:
                result_dir = self._run_rollout(
                    task=task,
                    source_tree=source_tree,
                    run_dir=run_dir,
                    run_id=run_id,
                    timeout=timeout or self.settings.timeout_s,
                    meta=meta,
                )
            except budget.BudgetExceeded as exc:
                current = store.read_json(run_dir / "meta.json") or meta
                current.update(
                    {
                        "status": "interrupted",
                        "finished_at": store.utc_now(),
                        "error": str(exc),
                        "budget": self.guard.snapshot(),
                    }
                )
                self._stamp_missing_outcome(current, bucket="interrupted", reason="budget")
                self._write_meta(run_dir, current)
                raise
            except Exception as exc:
                current = store.read_json(run_dir / "meta.json") or meta
                if current.get("status") == "running":
                    current["status"] = "failed"
                current.update(
                    {
                        "finished_at": store.utc_now(),
                        "error": f"{type(exc).__name__}: {exc}",
                        "budget": self.guard.snapshot(),
                    }
                )
                self._stamp_missing_outcome(current, bucket="unavailable", reason=str(exc))
                self._write_meta(run_dir, current)
                raise
        return result_dir

    def delivered(self, run_dir: Path) -> bool:
        meta = store.read_json(Path(run_dir) / "meta.json") or {}
        if "delivered" in meta:
            return bool(meta.get("delivered"))
        task_ref = meta.get("task_ref")
        if not task_ref:
            return False
        task = self.tasks_store.get(task_ref)
        workspace = Path(run_dir) / "workspace"
        summary = store.read_json(Path(run_dir) / "deliverables" / "summary.json") or {}
        return scoring.delivered(
            workspace, list(task.expected_deliverables), summary.get("changed") or {}
        )

    def trajectory(self, run_dir: Path, *, obs_trunc: int) -> str:
        path = self._trajectory_path(Path(run_dir))
        if not path.is_file():
            return "(no trajectory)"
        limit = max(200_000, obs_trunc * 4)
        rendered = self.substrate.read_trajectory(
            path, obs_trunc=obs_trunc, max_chars=limit
        )
        return truncate_marked(rendered, limit)

    def deliverable_files(self, run_dir: Path) -> dict[str, str]:
        payload = store.read_json(Path(run_dir) / "deliverables" / "files.json") or {}
        files = payload.get("files") if isinstance(payload, dict) else {}
        out: dict[str, str] = {}
        if isinstance(files, dict):
            for rel, row in files.items():
                if isinstance(row, dict) and isinstance(row.get("text"), str):
                    out[str(rel)] = row["text"]
        return out

    def deliverable_text(self, run_dir: Path) -> str:
        files = self.deliverable_files(run_dir)
        meta = store.read_json(Path(run_dir) / "meta.json") or {}
        task_ref = meta.get("task_ref")
        order: list[str] = []
        if task_ref:
            try:
                order = list(self.tasks_store.get(task_ref).expected_deliverables)
            except contract.ContractError:
                order = []
        chosen: list[str] = []
        for pattern in order:
            for rel in files:
                if rel == pattern or Path(rel).match(pattern):
                    if rel not in chosen:
                        chosen.append(rel)
        for rel in sorted(files):
            if rel not in chosen:
                chosen.append(rel)
        text = "\n\n".join(
            f"--- {rel} ---\n{files[rel]}" for rel in chosen[:50]
        )
        return text[:200_000]

    def score(self, run_dir: Path, task_id: str) -> dict | None:
        try:
            task = self.tasks_store.get(task_id)
            result = scoring.evaluate(Path(run_dir) / "workspace", list(task.checks))
        except Exception:
            return None
        if result is None:
            return None
        store.atomic_write_json(Path(run_dir) / "score.json", result)
        return result

    @property
    def reference_domain_order(self) -> list[str]:
        return sorted({self.domain_of(t) for t in self.tasks("all")})

    # --- hooks for concrete runtimes ----------------------------------------------
    def _hard_always_on_bytes(self) -> int:
        return 0

    def _runtime_version(self) -> str:
        raise NotImplementedError

    def _preflight_runtime(self) -> dict:
        return {}

    def _prepare_run(
        self,
        *,
        task: TaskContract,
        source_tree: Path,
        run_dir: Path,
        workspace: Path,
        run_id: str,
        meta: dict,
    ) -> PreparedRun:
        raise NotImplementedError

    def _invoke_turn(
        self,
        *,
        task: TaskContract,
        run_dir: Path,
        workspace: Path,
        runtime_home: Path,
        prompt: str,
        session_id: str | None,
        turn_index: int,
        timeout: int,
    ) -> TurnResult:
        raise NotImplementedError

    def _after_turn(self, run_dir: Path, result: TurnResult, turn_index: int) -> None:
        pass

    def _can_continue(self, result: TurnResult, session_id: str | None) -> bool:
        return bool(session_id) and result.can_continue

    def _trajectory_path(self, run_dir: Path) -> Path:
        raise NotImplementedError

    def controlled_variables(self) -> dict:
        controls = {
            "runtime": self.runtime,
            "runtime_version": self._runtime_version(),
            "model": self.model,
            "obs_trunc": self.settings.obs_trunc,
            "timeout_s": self.settings.timeout_s,
            "workspace_sha256": "per-task",
            "approver_policy": approvals.policy_hash(self.settings),
        }
        controls.update(self._runtime_controls())
        return controls

    def _runtime_controls(self) -> dict:
        return {}

    def preflight(self) -> dict:
        if self._preflight_cache is not None:
            return self._preflight_cache
        if not self.model:
            raise RuntimeError_(
                f"no model pinned for {self.runtime}; set "
                f"{config.MODEL_ENV[self.runtime]} or pass --model {self.runtime}=<id>"
            )
        version = self._runtime_version()
        pin = self.settings.pins.get(self.runtime, config.RUNTIME_PINS[self.runtime])
        if pin not in version:
            raise RuntimeError_(
                f"{self.runtime} version {version!r} does not match the pinned {pin!r}; "
                "upgrade deliberately and run the compatibility tasks first"
            )
        if self.settings.obs_trunc < 10_000:
            raise RuntimeError_("obs_trunc must be >= 10000")
        if not self.spec.leak_globs:
            raise RuntimeError_(
                "BenchSpec.leak_globs is empty; harness2-morphd refuses to run when criteria/answer "
                "paths are not declared"
            )
        base = self.base_harness()
        tree = self._ht.read_tree(self.substrate.surface(), base)
        self.validate_tree(tree)
        runtime_detail = self._preflight_runtime()
        controls = self.controlled_variables()
        detail = {
            "runtime": self.runtime,
            "version": version,
            "model": self.model,
            "base_harness": str(base),
            "substrate": self.substrate.name,
            "controls": controls,
            "controls_hash": store.payload_hash(controls),
        }
        detail.update(runtime_detail)
        self._preflight_cache = detail
        return detail

    # --- execution ----------------------------------------------------------------
    def _base_meta(
        self,
        task: TaskContract,
        run_id: str,
        harness_dir: Path | None,
        harness_hash: str,
        task_hash: str,
    ) -> dict:
        return {
            "run_id": run_id,
            "task_ref": task.task_ref,
            "domain": task.domain,
            "runtime": self.runtime,
            "substrate": self.substrate_name,
            "model": self.model,
            "runtime_version": self._runtime_version(),
            "harness_dir": str(harness_dir) if harness_dir is not None else None,
            "harness_hash": harness_hash,
            "task_hash": task_hash,
            "workspace_sha256": task.workspace_sha256,
            "status": "running",
            "started_at": store.utc_now(),
            "pricing": self.pricing.to_dict(),
            "approval_policy": approvals.policy_hash(self.settings),
            "safety": {
                "container_required": self.settings.container_required,
                "obs_trunc": self.settings.obs_trunc,
            },
        }

    def _write_meta(self, run_dir: Path, meta: dict) -> None:
        store.atomic_write_json(Path(run_dir) / "meta.json", meta)

    def _stamp_missing_outcome(self, meta: dict, *, bucket: str, reason: str) -> None:
        # C.3.2: meta.json always carries its required outcome fields; a run that failed or was
        # interrupted records them as unknown (null) rather than omitting them (C.3.3).
        meta.setdefault("achieved", None)
        meta.setdefault("delivered", None)
        meta.setdefault("bucket", bucket)
        meta.setdefault("reason", reason)
        meta.setdefault("usage", {"present": False})
        meta.setdefault("cost_usd", None)
        meta.setdefault("security", {"leaks": [], "secrets": []})
        meta.setdefault("approvals", None)
        meta.setdefault(
            "replay",
            {"turns_injected": 0, "completed_index": None, "not_converged": True, "aborted": True},
        )

    def _run_rollout(
        self,
        *,
        task: TaskContract,
        source_tree: Path,
        run_dir: Path,
        run_id: str,
        timeout: int,
        meta: dict,
    ) -> Path:
        workspace = run_dir / "workspace"
        shutil.copytree(
            self.tasks_store.workspace_dir(task.task_ref, verify=True), workspace
        )
        prepared = self._prepare_run(
            task=task,
            source_tree=source_tree,
            run_dir=run_dir,
            workspace=workspace,
            run_id=run_id,
            meta=meta,
        )
        if not workspace.is_dir():
            raise RuntimeError_("runtime did not leave a workspace")
        baseline = store.workspace_manifest(workspace)
        approvals.write_policy(run_dir / "approver.json", self.settings)
        controls = self.controlled_variables()
        meta.update(
            {
                "controls": controls,
                "controls_hash": store.payload_hash(controls),
                "profile_id": prepared.profile_id,
                "runtime_home": str(prepared.runtime_home),
                "workspace": str(workspace),
                "prepare_extras": prepared.extras,
                "baseline": {"sha256": baseline["sha256"], "n_files": baseline["n_files"]},
            }
        )
        self._write_meta(run_dir, meta)

        turns = list(task.user_turns)
        replay_steps: list[dict] = []
        results: list[TurnResult] = []
        usages: list[dict] = []
        spent = 0.0
        estimated_charges = 0
        session_id: str | None = None
        completed_index: int | None = None
        replay_aborted = ""
        for index, turn in enumerate(turns):
            if index > 0:
                previous = results[-1]
                if not self._can_continue(previous, session_id):
                    replay_aborted = previous.reason or "runtime cannot continue the session"
                    break
            self.guard.reserve(kind="rollout", label=f"{run_id}#{index + 1}")
            result = self._invoke_turn(
                task=task,
                run_dir=run_dir,
                workspace=workspace,
                runtime_home=prepared.runtime_home,
                prompt=turn.text,
                session_id=session_id,
                turn_index=index,
                timeout=timeout,
            )
            usage = pricing.normalize_usage(self.runtime, result.usage)
            cost = self.pricing.usage_cost(self.runtime, usage)
            estimated = cost is None
            if cost is None:
                cost = self.pricing.estimate_call_usd(self.runtime)
                estimated_charges += 1
            spent += cost
            self.guard.charge(
                cost,
                kind="rollout",
                label=f"{run_id}#{index + 1}",
                estimated=estimated,
            )
            usages.append(usage)
            results.append(result)
            session_id = result.session_id or session_id
            self._after_turn(run_dir, result, index)
            replay_steps.append(
                {
                    "index": index + 1,
                    "prompt": turn.text,
                    "prompt_sha256": store.sha256_text(turn.text),
                    "corrective": turn.corrective,
                    "session_id": result.session_id,
                    "exit_code": result.exit_code,
                    "achieved": result.achieved,
                    "reason": result.reason,
                    "bucket": result.bucket,
                    "turns": result.turns,
                    "usage": usage,
                    "cost_usd": round(cost, 6),
                    "cost_estimated": estimated,
                }
            )
            if result.achieved is True:
                completed_index = index + 1
                break

        last = results[-1] if results else None
        merged_usage = pricing.merge_usage(usages)
        after = store.workspace_manifest(workspace)
        changed = {
            rel: info
            for rel, info in after["files"].items()
            if baseline["files"].get(rel) != info
        }
        # Failed/interrupted runs must report unknown outcome fields as null, never as a
        # fabricated False/0 (PRD C.3.3).
        completed = completed_index is not None
        delivered = (
            scoring.delivered(workspace, list(task.expected_deliverables), changed)
            if completed
            else None
        )
        files_payload = self._deliverable_payload(workspace, changed)
        store.atomic_write_json(run_dir / "deliverables" / "files.json", files_payload)
        store.atomic_write_json(
            run_dir / "deliverables" / "summary.json",
            {
                "changed": changed,
                "n_changed": len(changed),
                "delivered": delivered,
                "expected": list(task.expected_deliverables),
            },
        )
        correction_messages = (
            sum(1 for step in replay_steps if step["corrective"]) if completed else None
        )
        total_turns = sum(max(1, r.turns) for r in results) if results else 0
        tool_events = [event for r in results for event in r.tool_events]
        facts = metrics.RunFacts(
            achieved=last.achieved if last else None,
            reason=last.reason if last else "no turns executed",
            bucket=metrics.bucket_of(last.reason if last else ""),
            turns=total_turns,
            usage=merged_usage,
            usage_present=bool(merged_usage.get("present")),
            cost_usd=round(spent, 6),
            delivered=delivered,
            convergence_turns=completed_index,
            correction_messages=correction_messages,
            tool_events=tool_events,
            final_text=last.final_text if last else "",
        )
        facts_payload = facts.to_dict()
        # C.3.3: an unmapped end reason becomes bucket "other", but that must be an explicit
        # warning, never a silent downgrade; and turn counts that the runtime did not report
        # are marked unavailable instead of quietly counted.
        warnings: list[str] = []
        raw_reason = last.reason if last else ""
        if facts.bucket == "other" and str(raw_reason) not in metrics.BUCKETS:
            warnings.append(f"unknown end reason {str(raw_reason)!r} mapped to bucket 'other'")
        turns_available = bool(results) and all(int(r.turns or 0) > 0 for r in results)
        facts_payload["warnings"] = warnings
        facts_payload["turns_available"] = turns_available
        store.atomic_write_json(run_dir / "facts.json", facts_payload)
        metrics_payload = metrics.compute_metrics(facts)
        metrics_payload["n_estimated_cost_calls"] = estimated_charges
        metrics_payload["warnings"] = warnings
        metrics_payload["turns_available"] = turns_available
        store.atomic_write_json(run_dir / "metrics.json", metrics_payload)
        replay_payload = {
            "runtime": self.runtime,
            "task_ref": task.task_ref,
            "turns": len(turns),
            "injected": replay_steps,
            "completion_index": completed_index,
            "convergence_turns": completed_index,
            "not_converged": completed_index is None,
            "replay_aborted": replay_aborted,
            "correction_messages": correction_messages,
        }
        store.atomic_write_json(run_dir / "replay.json", replay_payload)

        trajectory_text = ""
        try:
            trajectory_text = self.trajectory(run_dir, obs_trunc=self.settings.obs_trunc)
        except Exception:
            trajectory_text = ""
        leak_problems = security.assert_no_leaks(
            task.hidden_terms(),
            task.instruction,
            workspace,
            trajectory_text,
            leak_globs=tuple(self.spec.leak_globs),
        )
        secret_findings = sanitize.scan_paths([run_dir], max_file_size=1_000_000)
        security_payload = {
            "leaks": leak_problems,
            "secrets": [finding.to_dict() for finding in secret_findings],
        }
        approvals_path = run_dir / "approvals.jsonl"
        approval_stats = approvals.distribution(approvals_path)
        meta.update(
            {
                "status": "security-failed" if (leak_problems or secret_findings) else "done",
                "finished_at": store.utc_now(),
                "session_id": session_id,
                "completed_index": completed_index,
                "achieved": facts.achieved,
                "bucket": facts.bucket,
                "reason": facts.reason,
                "delivered": delivered,
                "changed_files": len(changed),
                "usage": merged_usage,
                "cost_usd": round(spent, 6),
                "estimated_cost_calls": estimated_charges,
                "budget": self.guard.snapshot(),
                "security": security_payload,
                "approvals": approval_stats,
                "replay": {
                    "turns_injected": len(replay_steps),
                    "completed_index": completed_index,
                    "not_converged": completed_index is None,
                    "aborted": replay_aborted,
                },
                "duration_s": round(time.time() - _started(meta), 3),
            }
        )
        self._write_meta(run_dir, meta)
        if leak_problems or secret_findings:
            raise security.SecurityError(
                "run produced security violations: "
                + "; ".join(leak_problems + [f"{f.path}:{f.line} {f.kind}" for f in secret_findings])
            )
        store.atomic_write_json(
            run_dir / ".morphd-complete",
            {
                "run_id": run_id,
                "runtime": self.runtime,
                "harness_hash": meta["harness_hash"],
                "task_hash": meta["task_hash"],
                "model": self.model,
                "runtime_version": meta.get("runtime_version") or "",
                "at": store.utc_now(),
            },
        )
        return run_dir

    def _deliverable_payload(self, workspace: Path, changed: dict) -> dict:
        files: dict[str, dict] = {}
        for rel, info in sorted(changed.items()):
            path = workspace / rel
            text = ""
            binary = False
            try:
                data = path.read_bytes()
                if b"\x00" in data[:8192]:
                    binary = True
                else:
                    text = data.decode("utf-8", errors="replace")[:500_000]
            except OSError:
                binary = True
            files[rel] = {**info, "text": text, "binary": binary}
        return {"files": files, "n_files": len(files)}


def _started(meta: dict) -> float:
    from datetime import datetime

    raw = meta.get("started_at")
    if not raw:
        return time.time()
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return time.time()
