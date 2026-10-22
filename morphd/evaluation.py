"""Shadow evaluation: base vs candidate on the same tasks, one runtime at a time."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import approvals, budget, contract, metrics, pricing, store
from .contract import TaskStore


@dataclass
class EvaluationOptions:
    splits: tuple[str, ...] = ("test", "holdout")
    max_tasks: int | None = None
    only_domains: tuple[str, ...] = ()
    only_tasks: tuple[str, ...] = ()
    timeout: int | None = None


class EvaluationError(RuntimeError):
    pass


def _turn_cost(row: dict, task: contract.TaskContract) -> int:
    convergence = row.get("convergence_turns")
    if convergence is None:
        return task.max_turns() + 1
    return int(convergence)


def _subset(adapter, tasks_store: TaskStore, options: EvaluationOptions) -> list[str]:
    selected: list[str] = []
    for split in options.splits:
        try:
            selected.extend(tasks_store.tasks(split))
        except contract.ContractError as exc:
            if split != "test":
                raise
            raise EvaluationError(str(exc)) from exc
    selected = sorted(set(selected))
    if options.only_domains:
        keep = set(options.only_domains)
        missing = keep - {tasks_store.domain_of(t) for t in selected}
        if missing:
            raise EvaluationError(f"unknown domains in evaluation: {sorted(missing)}")
        selected = [t for t in selected if tasks_store.domain_of(t) in keep]
    if options.only_tasks:
        keep_tasks = set(options.only_tasks)
        missing = keep_tasks - set(selected)
        if missing:
            raise EvaluationError(f"unknown tasks in evaluation: {sorted(missing)}")
        selected = [t for t in selected if t in keep_tasks]
    if options.max_tasks is not None:
        selected = selected[: max(0, int(options.max_tasks))]
    if not selected:
        raise EvaluationError("evaluation selected no tasks")
    return selected


def _read_run(adapter, run_dir: Path, task_ref: str, domain: str) -> dict:
    row_metrics = store.read_json(Path(run_dir) / "metrics.json") or {}
    meta = store.read_json(Path(run_dir) / "meta.json") or {}
    facts = store.read_json(Path(run_dir) / "facts.json") or {}
    status = meta.get("status") or "missing"
    usable = status == "done"
    row = {
        "task_ref": task_ref,
        "domain": domain,
        "run_dir": str(run_dir),
        "achieved": row_metrics.get("achieved") if usable else None,
        "delivered": (bool(row_metrics.get("delivered")) if usable else None),
        "convergence_turns": row_metrics.get("convergence_turns") if usable else None,
        "correction_messages": int(row_metrics.get("correction_messages") or 0),
        "tool_error_loop": bool(row_metrics.get("tool_error_loop")),
        "turns": int(row_metrics.get("turns") or 0),
        "bucket": row_metrics.get("bucket") or "unavailable",
        "cost_usd": row_metrics.get("cost_usd"),
        "usage_present": bool(row_metrics.get("usage_present")),
        "error": meta.get("error") or "",
        "status": status,
        "approvals": meta.get("approvals") or {},
        "facts": facts,
    }
    return row


def run_evaluation(
    *,
    adapter,
    tasks_store: TaskStore,
    options: EvaluationOptions,
    candidate: Path,
    guard: budget.BudgetGuard,
    price: pricing.Pricing,
    settings,
    stamp: str,
) -> dict:
    candidate = Path(candidate)
    if not candidate.is_dir():
        raise EvaluationError(f"candidate harness is not a directory: {candidate}")
    tree = adapter._ht.read_tree(adapter.substrate.surface(), candidate)
    adapter.validate_tree(tree)
    candidate_hash = store.payload_hash(tree)
    if not adapter.model:
        raise EvaluationError(f"no model configured for {adapter.runtime}")
    adapter.preflight()
    task_refs = _subset(adapter, tasks_store, options)
    rows: list[dict] = []
    status = "done"
    for index, task_ref in enumerate(task_refs, 1):
        task = tasks_store.get(task_ref)
        base_id = f"eval-{stamp}-{index}-{task_ref}-base"
        cand_id = f"eval-{stamp}-{index}-{task_ref}-cand"
        base_row: dict
        cand_row: dict
        try:
            base_dir = adapter.execute(task_ref, None, base_id, timeout=options.timeout)
            base_row = _read_run(adapter, base_dir, task_ref, task.domain)
        except budget.BudgetExceeded:
            status = "interrupted"
            raise
        except Exception as exc:
            base_row = {
                "task_ref": task_ref,
                "domain": task.domain,
                "achieved": None,
                "delivered": None,
                "convergence_turns": None,
                "correction_messages": 0,
                "tool_error_loop": False,
                "turns": 0,
                "bucket": "unavailable",
                "cost_usd": None,
                "usage_present": False,
                "error": f"{type(exc).__name__}: {exc}",
                "status": "failed",
            }
        try:
            cand_dir = adapter.execute(task_ref, candidate, cand_id, timeout=options.timeout)
            cand_row = _read_run(adapter, cand_dir, task_ref, task.domain)
        except budget.BudgetExceeded:
            status = "interrupted"
            rows.append({"task_ref": task_ref, "domain": task.domain, "base": base_row, "candidate": None})
            raise
        except Exception as exc:
            cand_row = {
                "task_ref": task_ref,
                "domain": task.domain,
                "achieved": None,
                "delivered": None,
                "convergence_turns": None,
                "correction_messages": 0,
                "tool_error_loop": False,
                "turns": 0,
                "bucket": "unavailable",
                "cost_usd": None,
                "usage_present": False,
                "error": f"{type(exc).__name__}: {exc}",
                "status": "failed",
            }
        for row in (base_row, cand_row):
            if row.get("status") == "failed":
                row["convergence_cost"] = None
                row["tool_error_loop_int"] = None
                row["turns"] = None
                row["correction_messages"] = None
            else:
                if row.get("convergence_turns") is not None:
                    row["convergence_cost"] = int(row["convergence_turns"])
                else:
                    row["convergence_cost"] = task.max_turns() + 1
                row["tool_error_loop_int"] = 1 if row.get("tool_error_loop") else 0
        rows.append(
            {
                "task_ref": task_ref,
                "domain": task.domain,
                "base": base_row,
                "candidate": cand_row,
            }
        )
    if status == "done" and any(
        (row.get("base") or {}).get("status") == "failed"
        or (row.get("candidate") or {}).get("status") == "failed"
        for row in rows
    ):
        status = "partial"
    base_rows = [row["base"] for row in rows]
    cand_rows = [row["candidate"] for row in rows if row.get("candidate")]
    if len(cand_rows) != len(base_rows):
        status = "interrupted"

    def approve_stats(items):
        allowed = sum(int((r.get("approvals") or {}).get("allowed") or 0) for r in items)
        rejected = sum(int((r.get("approvals") or {}).get("rejected") or 0) for r in items)
        total = allowed + rejected
        return {
            "n": total,
            "allowed": allowed,
            "rejected": rejected,
            "allow_rate": round(allowed / total, 4) if total else None,
        }

    base_approvals = approve_stats(base_rows)
    cand_approvals = approve_stats(cand_rows)
    paired_keys = (
        "achieved",
        "delivered",
        "convergence_cost",
        "correction_messages",
        "tool_error_loop_int",
        "turns",
    )
    deltas = {
        key: metrics.paired_delta(base_rows, cand_rows, key) for key in paired_keys
    }
    domains = sorted({row["domain"] for row in rows})
    by_domain = metrics.domain_breakdown(base_rows, cand_rows, domains)
    report = {
        "report_contract": 1,
        "runtime": adapter.runtime,
        "created_at": store.utc_now(),
        "status": status,
        "candidate": str(candidate),
        "candidate_hash": candidate_hash,
        "base_harness": str(adapter.base_harness()),
        "controls_hash": store.payload_hash(adapter.controlled_variables()),
        "controls": adapter.controlled_variables(),
        "permissions_hash": approvals.policy_hash(settings),
        "approvals": {"base": base_approvals, "candidate": cand_approvals},
        "model": adapter.model,
        "splits": list(options.splits),
        "pricing": price.to_dict(),
        "budget": guard.snapshot(),
        "n_tasks": len(rows),
        "base": metrics.aggregate(base_rows),
        "candidate_metrics": metrics.aggregate(cand_rows),
        "deltas": deltas,
        "domains": by_domain,
        "anomalies": {
            "base": metrics.anomaly_distribution(base_rows),
            "candidate": metrics.anomaly_distribution(cand_rows),
        },
        "rows": rows,
    }
    return report


def check_gate(report: dict, *, phase: str = "m1", min_tasks: int | None = None) -> dict:
    failures: list[str] = []
    warnings: list[str] = []
    if report.get("status") != "done":
        failures.append(f"evaluation status is {report.get('status')!r}, not done")
    n = int(report.get("n_tasks") or 0)
    phase = phase.lower()
    if phase not in ("m1", "m2"):
        raise EvaluationError("phase must be m1 or m2")
    required_n = 20 if phase == "m1" else 50
    if min_tasks is not None:
        required_n = int(min_tasks)
    if n < required_n:
        failures.append(f"n={n} is below the {phase.upper()} minimum of {required_n} tasks")
    deltas = report.get("deltas") or {}
    achieved = deltas.get("achieved") or {}
    delivered = deltas.get("delivered") or {}
    n_known = int(achieved.get("n") or 0)
    if n and n_known < max(1, int(0.8 * n)):
        failures.append(
            f"only {n_known}/{n} task pairs have a usable completion signal; n/a must not be "
            "silently filled with zero"
        )
    delta = achieved.get("delta")
    if delta is None:
        failures.append("no paired achievement delta")
    elif delta <= 0:
        failures.append(f"achievement delta is {delta:+.4f}; direction is not an improvement")
    ci = achieved.get("ci95")
    if ci and ci[1] <= 0:
        failures.append(f"achievement 95% CI {ci} includes only non-improvement")
    delivered_delta = delivered.get("delta")
    if delivered_delta is not None and delivered_delta < 0:
        failures.append(
            f"deliverable presence regressed by {delivered_delta:+.4f}; a faster harness that "
            "stops producing artifacts is a regression"
        )
    for domain, row in (report.get("domains") or {}).items():
        d = (row.get("achieved_delta") or {}).get("delta")
        if d is not None and d < -0.05:
            failures.append(f"domain {domain!r} achievement regressed by {d:+.4f} (> 5pp)")
    approval_stats = report.get("approvals") or {}
    drift_info = approvals.drift(
        approval_stats.get("base") or {}, approval_stats.get("candidate") or {}
    )
    if drift_info.get("drift"):
        message = (
            "answerer allow-rate drift "
            f"{drift_info['delta']:+.4f} exceeds 10pp"
        )
        if (
            int((approval_stats.get("base") or {}).get("n") or 0) >= 10
            and int((approval_stats.get("candidate") or {}).get("n") or 0) >= 10
        ):
            failures.append(message)
        else:
            warnings.append(message + " (small sample; human review)")
    if not report.get("permissions_hash"):
        failures.append("permission-baseline hash is missing from the evaluation report")
    if phase == "m2":
        relative = achieved.get("relative")
        base_rate = achieved.get("base")
        cand_rate = achieved.get("candidate")
        if relative is None:
            if not (base_rate == 0 and cand_rate and cand_rate > 0):
                failures.append(
                    "M2 requires >= 20% relative achievement improvement, but the "
                    "relative change is undefined"
                )
        elif relative < 0.20:
            failures.append(
                f"M2 requires >= 20% relative achievement improvement, got {relative}"
            )
        if ci and ci[0] <= 0:
            failures.append(f"M2 requires a CI lower bound above zero, got {ci}")
    else:
        if ci and ci[0] <= 0:
            warnings.append(
                f"M1 confidence interval {ci} still includes zero; human review is mandatory"
            )
    return {
        "phase": phase,
        "passed": not failures,
        "failures": failures,
        "warnings": warnings,
        "n": n,
        "required_n": required_n,
        "achieved_delta": achieved.get("delta"),
        "achieved_ci95": ci,
        "delivered_delta": delivered_delta,
    }


def render_markdown(report: dict, gate: dict | None = None) -> str:
    runtime = report.get("runtime")
    lines = [
        f"# Evaluation — {runtime}",
        "",
        f"- candidate: `{report.get('candidate')}`",
        f"- tasks: {report.get('n_tasks')} ({', '.join(report.get('splits') or [])})",
        f"- model: `{report.get('model')}`",
        f"- candidate hash: `{report.get('candidate_hash')}`",
        "",
        "## Paired deltas (candidate − base)",
        "",
        "| metric | n | base | candidate | delta | 95% CI |",
        "|---|---|---|---|---|---|",
    ]
    labels = {
        "achieved": "achievement",
        "delivered": "deliverable presence",
        "convergence_cost": "convergence cost",
        "correction_messages": "correction messages",
        "tool_error_loop_int": "tool error loop",
        "turns": "turn efficiency",
    }
    for key, row in (report.get("deltas") or {}).items():
        lines.append(
            f"| {labels.get(key, key)} | {row.get('n')} | {row.get('base')} | "
            f"{row.get('candidate')} | {row.get('delta')} | {row.get('ci95')} |"
        )
    lines += [
        "",
        "## By domain",
        "",
        "| domain | base achieved | candidate achieved | delta | base delivered | candidate delivered |",
        "|---|---|---|---|---|---|",
    ]
    for domain, row in (report.get("domains") or {}).items():
        b = row.get("base") or {}
        c = row.get("candidate") or {}
        d = (row.get("achieved_delta") or {}).get("delta")
        lines.append(
            f"| {domain} | {b.get('achievement_rate')} | {c.get('achievement_rate')} | "
            f"{d} | {b.get('delivered_rate')} | {c.get('delivered_rate')} |"
        )
    if gate:
        lines += [
            "",
            f"## Gate ({gate.get('phase', '').upper()})",
            "",
            f"- passed: **{gate.get('passed')}**",
        ]
        for failure in gate.get("failures") or []:
            lines.append(f"- failure: {failure}")
        for warning in gate.get("warnings") or []:
            lines.append(f"- warning: {warning}")
    lines += [
        "",
        "> Results are valid only within this runtime. Do not compare absolute values across "
        "dsh, Codex and Claude Code.",
        "",
    ]
    return "\n".join(lines)


def save_report(report: dict, path: Path) -> Path:
    path = Path(path)
    store.atomic_write_json(path, report)
    return path
