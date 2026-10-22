"""Per-runtime reporting and the self-hosted judge entrypoint."""

from __future__ import annotations

from . import library, metrics, parity, store
from .config import Settings


def _load_runs(settings: Settings, runtime: str) -> list[dict]:
    root = settings.runs_root / runtime / "rollouts"
    rows: list[dict] = []
    if not root.is_dir():
        return rows
    for meta_path in sorted(root.rglob("meta.json")):
        meta = store.read_json(meta_path)
        if not isinstance(meta, dict):
            continue
        run_dir = meta_path.parent
        metric = store.read_json(run_dir / "metrics.json") or {}
        replay = store.read_json(run_dir / "replay.json") or {}
        run_id = str(meta.get("run_id") or "")
        row = {
            "task_ref": meta.get("task_ref"),
            "domain": meta.get("domain") or "unknown",
            "label": run_id.rsplit("/", 1)[-1] if run_id else "unknown",
            "run_dir": str(run_dir),
            "status": meta.get("status"),
            "model": meta.get("model"),
            "achieved": metric.get("achieved", meta.get("achieved")),
            "delivered": metric.get("delivered", meta.get("delivered")),
            "bucket": metric.get("bucket") or meta.get("bucket") or "unavailable",
            "convergence_turns": replay.get("convergence_turns"),
            "correction_messages": replay.get("correction_messages", 0),
            "tool_error_loop": metric.get("tool_error_loop", False),
            "turns": metric.get("turns", 0),
            "cost_usd": meta.get("cost_usd"),
            "usage": meta.get("usage") or {},
            "usage_present": bool(meta.get("usage", {}).get("present")),
            "finished_at": meta.get("finished_at") or meta.get("started_at") or "",
            "approvals": meta.get("approvals") or {},
            "security": meta.get("security") or {},
        }
        score = store.read_json(run_dir / "score.json")
        if isinstance(score, dict):
            row["score"] = {
                "n_passed": score.get("n_passed"),
                "n_criteria": score.get("n_criteria"),
                "all_pass": score.get("all_pass"),
            }
        rows.append(row)
    return rows


def _day(row: dict) -> str:
    value = str(row.get("finished_at") or "")
    return value[:10] if len(value) >= 10 else "unknown"


def _cost_table(rows: list[dict]) -> dict:
    out: dict[str, dict[str, float]] = {}
    for row in rows:
        day = _day(row)
        domain = str(row.get("domain") or "unknown")
        cost = row.get("cost_usd")
        if cost is None:
            continue
        out.setdefault(day, {}).setdefault(domain, 0.0)
        out[day][domain] = round(out[day][domain] + float(cost), 6)
    return {day: dict(sorted(domains.items())) for day, domains in sorted(out.items())}


def _load_budget(settings: Settings, runtime: str) -> dict:
    root = settings.runs_root / runtime / "adapt" / "morphd-runs"
    latest: dict[str, tuple[str, dict]] = {}
    records = 0
    if root.is_dir():
        for path in sorted(root.glob("*.json")):
            record = store.read_json(path) or {}
            records += 1
            namespace = str(record.get("namespace") or path.name)
            finished = str(record.get("finished_at") or record.get("started_at") or "")
            if namespace not in latest or finished >= latest[namespace][0]:
                latest[namespace] = (finished, record)
    calls = 0
    usd = 0.0
    statuses: dict[str, int] = {}
    for _, record in latest.values():
        budget = record.get("budget") or {}
        calls += int(budget.get("calls") or 0)
        usd += float(budget.get("usd") or 0.0)
        status = str(record.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "run_records": records,
        "guard_calls": calls,
        "guard_usd": round(usd, 6),
        "statuses": dict(sorted(statuses.items())),
    }


def collect(settings: Settings, runtimes: list[str]) -> dict:
    per_runtime: dict[str, dict] = {}
    evaluations: dict[str, list[dict]] = {}
    for runtime in runtimes:
        rows = _load_runs(settings, runtime)
        runnable = [
            row for row in rows if row.get("status") in {"done", "security-failed", "interrupted"}
        ]
        aggregate = metrics.aggregate(runnable)
        aggregate["anomalies"] = metrics.anomaly_distribution(runnable)
        domains = sorted({str(row["domain"]) for row in runnable})
        by_domain = {}
        for domain in domains:
            subset = [row for row in runnable if row["domain"] == domain]
            by_domain[domain] = metrics.aggregate(subset)
        by_label = {}
        for label in sorted({str(row.get("label") or "unknown") for row in runnable}):
            subset = [row for row in runnable if row.get("label") == label]
            by_label[label] = metrics.aggregate(subset)
        approval_allowed = sum(
            int((row.get("approvals") or {}).get("allowed") or 0) for row in runnable
        )
        approval_rejected = sum(
            int((row.get("approvals") or {}).get("rejected") or 0) for row in runnable
        )
        tokens = {
            "input_tokens": sum(int((row.get("usage") or {}).get("input_tokens") or 0) for row in runnable),
            "output_tokens": sum(int((row.get("usage") or {}).get("output_tokens") or 0) for row in runnable),
            "n_missing_usage": sum(1 for row in runnable if not row.get("usage_present")),
        }
        per_runtime[runtime] = {
            "n_runs": len(rows),
            "n_runnable": len(runnable),
            "statuses": _counts(row.get("status") for row in rows),
            "metrics": aggregate,
            "domains": by_domain,
            "by_label": by_label,
            "cost_by_day": _cost_table(runnable),
            "tokens": tokens,
            "approvals": {
                "allowed": approval_allowed,
                "rejected": approval_rejected,
                "allow_rate": round(approval_allowed / (approval_allowed + approval_rejected), 4)
                if approval_allowed + approval_rejected
                else None,
            },
            "security_violations": sum(
                1
                for row in runnable
                if (row.get("security") or {}).get("leaks")
                or (row.get("security") or {}).get("secrets")
            ),
            "budget": _load_budget(settings, runtime),
            "library": library.stats(settings.runtime_library_root(runtime)),
            "runs": runnable,
        }
        eval_rows = []
        eval_root = settings.evaluations_root / runtime
        if eval_root.is_dir():
            for path in sorted(eval_root.glob("*.json")):
                report = store.read_json(path)
                if isinstance(report, dict) and report.get("report_contract") == 1:
                    eval_rows.append(
                        {
                            "path": str(path),
                            "created_at": report.get("created_at"),
                            "status": report.get("status"),
                            "candidate_hash": report.get("candidate_hash"),
                            "n_tasks": report.get("n_tasks"),
                            "achieved_delta": (report.get("deltas") or {}).get("achieved"),
                            "delivered_delta": (report.get("deltas") or {}).get("delivered"),
                        }
                    )
        evaluations[runtime] = eval_rows
    return {
        "report_contract": 1,
        "generated_at": store.utc_now(),
        "runtimes": per_runtime,
        "evaluations": evaluations,
        "parity": parity.collect(settings, runtimes),
        "note": (
            "Absolute metrics are runtime-internal. Do not rank dsh, Codex and Claude Code "
            "against each other."
        ),
    }


def _counts(values) -> dict:
    out: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def render_markdown(report: dict) -> str:
    lines = [
        "# morphd report",
        "",
        f"Generated: {report.get('generated_at')}",
        "",
        "> Runtime-internal only. Cross-runtime absolute ranking is not valid.",
        "",
    ]
    for runtime, data in (report.get("runtimes") or {}).items():
        agg = data.get("metrics") or {}
        lines += [
            f"## {runtime}",
            "",
            f"- runs: {data.get('n_runs')} ({data.get('n_runnable')} usable)",
            f"- statuses: {data.get('statuses')}",
            f"- achievement rate: {agg.get('achievement_rate')} "
            f"(known n={agg.get('n_achieved_known')})",
            f"- delivered rate: {agg.get('delivered_rate')}",
            f"- convergence mean: {agg.get('convergence_mean')} "
            f"(converged n={agg.get('n_converged')})",
            f"- correction mean: {agg.get('correction_mean')}",
            f"- tool error loop rate: {agg.get('tool_error_loop_rate')}",
            f"- turns mean: {agg.get('turn_efficiency_mean')}",
            f"- rollout USD (sum): {agg.get('cost_usd_sum')}",
            f"- budget guard: calls={data.get('budget', {}).get('guard_calls')} "
            f"usd={data.get('budget', {}).get('guard_usd')} "
            f"(includes proposer sessions; statuses={data.get('budget', {}).get('statuses')})",
            f"- tokens: input={data['tokens']['input_tokens']} "
            f"output={data['tokens']['output_tokens']} "
            f"missing-usage runs={data['tokens']['n_missing_usage']}",
            f"- approvals: allow={data['approvals']['allowed']} "
            f"deny={data['approvals']['rejected']} "
            f"allow-rate={data['approvals']['allow_rate']}",
            f"- security violations: {data.get('security_violations')}",
            f"- library: {data.get('library')}",
            "",
            "| domain | runs | achievement | delivered | avg turns | USD |",
            "|---|---|---|---|---|---|",
        ]
        for domain, row in (data.get("domains") or {}).items():
            lines.append(
                f"| {domain} | {row.get('n')} | {row.get('achievement_rate')} | "
                f"{row.get('delivered_rate')} | {row.get('turn_efficiency_mean')} | "
                f"{row.get('cost_usd_sum')} |"
            )
        lines += ["", "### By arm", ""]
        lines += ["| label | runs | achievement | delivered | avg turns |", "|---|---|---|---|---|"]
        for label, row in (data.get("by_label") or {}).items():
            lines.append(
                f"| {label} | {row.get('n')} | {row.get('achievement_rate')} | "
                f"{row.get('delivered_rate')} | {row.get('turn_efficiency_mean')} |"
            )
        lines += ["", "### Cost by day and domain (USD)", ""]
        cost_by_day = data.get("cost_by_day") or {}
        if not cost_by_day:
            lines.append("_no costed runs_")
        for day, domains in cost_by_day.items():
            for domain, usd in domains.items():
                lines.append(f"- {day} / {domain}: {usd:.4f}")
        lines.append("")
        evals = (report.get("evaluations") or {}).get(runtime) or []
        if evals:
            lines += ["### Shadow evaluations", ""]
            for ev in evals:
                lines.append(
                    f"- {ev.get('created_at')}: n={ev.get('n_tasks')} status={ev.get('status')} "
                    f"achieved-delta={((ev.get('achieved_delta') or {}).get('delta'))}"
                )
            lines.append("")
    parity_data = report.get("parity") or {}
    if parity_data.get("runtimes"):
        lines += ["## Runtime parity", "", "| runtime | available | replay converged | done |", "|---|---|---|---|"]
        for runtime, row in (parity_data.get("runtimes") or {}).items():
            lines.append(
                f"| {runtime} | {row.get('task_available')} | {row.get('replay_converged')} | "
                f"{row.get('n_done')} |"
            )
        lines += ["", "| pair | verdict agree | common | adapter skew |", "|---|---|---|---|"]
        for pair, row in (parity_data.get("pairs") or {}).items():
            lines.append(
                f"| {pair} | {row.get('verdict_agree')} | {row.get('n_common_verdicts')} | "
                f"{row.get('adapter_skew')} |"
            )
        lines.append("")
        lines.append("> Parity is diagnostic, not a cross-runtime ranking.")
        lines.append("")
    return "\n".join(lines)


def judge_runs(*, adapter, settings: Settings, force: bool = False) -> dict:
    runtime = adapter.runtime
    root = settings.runs_root / runtime / "rollouts"
    scored = 0
    unscored = 0
    skipped = 0
    for meta_path in sorted(root.rglob("meta.json")) if root.is_dir() else []:
        meta = store.read_json(meta_path) or {}
        if meta.get("status") not in {"done", "security-failed"}:
            continue
        task_ref = meta.get("task_ref")
        run_dir = meta_path.parent
        output = run_dir / "judge.json"
        previous = store.read_json(output)
        if isinstance(previous, dict) and previous.get("n_criteria") and not force:
            skipped += 1
            continue
        score = adapter.score(run_dir, task_ref)
        if score is None:
            unscored += 1
            store.append_jsonl(
                run_dir / "judge-attempts.jsonl",
                {"at": store.utc_now(), "result": "unscored", "task_ref": task_ref},
            )
            continue
        store.atomic_write_json(
            output,
            {
                **score,
                "task_ref": task_ref,
                "runtime": runtime,
                "judged_at": store.utc_now(),
            },
        )
        scored += 1
    return {
        "runtime": runtime,
        "scored": scored,
        "unscored": unscored,
        "skipped": skipped,
    }
