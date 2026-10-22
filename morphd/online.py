"""The online arm: trends from real dsh sessions, never a promotion gate."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from . import tasksource


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = min(len(values) - 1, int(round(0.95 * (len(values) - 1))))
    return round(values[index], 1)


def _day(timestamp_ms) -> str:
    try:
        return datetime.fromtimestamp(float(timestamp_ms) / 1000, tz=timezone.utc).strftime(
            "%Y-%m-%d"
        )
    except (TypeError, ValueError, OSError):
        return "unknown"


def collect(sessions_root: Path) -> dict:
    rows = []
    skipped = 0
    for path in tasksource.discover_sessions(Path(sessions_root)):
        try:
            finding = tasksource.read_session(path)
        except tasksource.SessionReadError:
            skipped += 1
            continue
        if not finding.turns:
            skipped += 1
            continue
        times = [
            event.get("time")
            for event in finding.events
            if isinstance(event.get("time"), (int, float))
        ]
        duration = (max(times) - min(times)) if len(times) >= 2 else None
        corrections = sum(1 for turn in finding.turns[1:] if turn.corrective)
        rows.append(
            {
                "session_id": finding.session_id,
                "day": _day(min(times) if times else None),
                "cwd": finding.cwd,
                "n_user_turns": len(finding.turns),
                "corrections": corrections,
                "failed": finding.failed,
                "failures": finding.failures,
                "rejected_approvals": finding.rejected_approvals,
                "duration_ms": duration,
            }
        )
    n = len(rows)
    by_day: dict[str, dict] = {}
    for row in rows:
        day = row["day"]
        bucket = by_day.setdefault(day, {"sessions": 0, "with_corrections": 0, "failed": 0})
        bucket["sessions"] += 1
        bucket["with_corrections"] += 1 if row["corrections"] else 0
        bucket["failed"] += 1 if row["failed"] else 0
    durations = [float(row["duration_ms"]) for row in rows if row["duration_ms"]]
    return {
        "online_contract": 1,
        "sessions_root": str(sessions_root),
        "n_sessions": n,
        "n_skipped": skipped,
        "n_with_corrections": sum(1 for row in rows if row["corrections"]),
        "correction_session_rate": round(
            sum(1 for row in rows if row["corrections"]) / n, 4
        )
        if n
        else None,
        "correction_turns_mean": round(sum(row["corrections"] for row in rows) / n, 3)
        if n
        else None,
        "intervention_rate": round(
            sum(1 for row in rows if row["n_user_turns"] > 1) / n, 4
        )
        if n
        else None,
        "failed_session_rate": round(sum(1 for row in rows if row["failed"]) / n, 4)
        if n
        else None,
        "rejected_approvals": sum(row["rejected_approvals"] for row in rows),
        "duration_p95_ms": _p95(durations),
        "n_with_duration": len(durations),
        "by_day": dict(sorted(by_day.items())),
        "rows": rows,
        "note": "Online trends are not used for promotion gates; samples are uncontrolled.",
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Online arm (dsh sessions)",
        "",
        f"- sessions root: `{report.get('sessions_root')}`",
        f"- sessions: {report.get('n_sessions')} (skipped {report.get('n_skipped')})",
        f"- correction session rate: {report.get('correction_session_rate')}",
        f"- correction turns mean: {report.get('correction_turns_mean')}",
        f"- intervention rate: {report.get('intervention_rate')}",
        f"- failed session rate: {report.get('failed_session_rate')}",
        f"- rejected approvals: {report.get('rejected_approvals')}",
        f"- turn-to-end p95: {report.get('duration_p95_ms')} ms "
        f"(n={report.get('n_with_duration')})",
        "",
        "| day | sessions | with corrections | failed |",
        "|---|---|---|---|",
    ]
    for day, row in (report.get("by_day") or {}).items():
        lines.append(
            f"| {day} | {row['sessions']} | {row['with_corrections']} | {row['failed']} |"
        )
    lines += [
        "",
        "> Online trends are observational and are not used for promotion gates.",
        "",
    ]
    return "\n".join(lines)


__all__ = ["collect", "render_markdown"]
