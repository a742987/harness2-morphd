"""Offline-arm metrics and paired comparison statistics."""

from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass, field

BUCKETS = (
    "completed",
    "error",
    "blocked",
    "max-tokens",
    "aborted",
    "interrupted",
    "forked",
    "other",
    "unavailable",
)


def tool_error_loops(tool_events: list[dict]) -> int:
    loops = 0
    streak = 0
    previous: tuple[str, str] | None = None
    for event in tool_events:
        if not event.get("error"):
            previous = None
            streak = 0
            continue
        signature = (str(event.get("tool") or ""), str(event.get("signature") or ""))
        if signature == previous:
            streak += 1
        else:
            previous = signature
            streak = 1
        if streak >= 3:
            loops += 1
            streak = 0
    return loops


@dataclass
class RunFacts:
    achieved: bool | None
    reason: str = ""
    bucket: str = "unavailable"
    turns: int = 0
    usage: dict = field(default_factory=dict)
    usage_present: bool = False
    cost_usd: float | None = None
    delivered: bool = False
    convergence_turns: int | None = None
    correction_messages: int = 0
    tool_events: list[dict] = field(default_factory=list)
    final_text: str = ""

    def to_dict(self) -> dict:
        return {
            "achieved": self.achieved,
            "reason": self.reason,
            "bucket": self.bucket,
            "turns": self.turns,
            "usage": self.usage,
            "usage_present": self.usage_present,
            "cost_usd": self.cost_usd,
            "delivered": self.delivered,
            "convergence_turns": self.convergence_turns,
            "correction_messages": self.correction_messages,
            "tool_error_loops": tool_error_loops(self.tool_events),
            "final_text": self.final_text,
        }


def bucket_of(kind: str | None) -> str:
    if kind in BUCKETS:
        return str(kind)
    if not kind:
        return "unavailable"
    return "other"


def compute_metrics(facts: RunFacts) -> dict:
    return {
        "achieved": facts.achieved,
        "bucket": facts.bucket,
        "reason": facts.reason,
        "delivered": facts.delivered,
        "convergence_turns": facts.convergence_turns,
        "correction_messages": facts.correction_messages,
        "tool_error_loop": tool_error_loops(facts.tool_events) > 0,
        "turns": facts.turns,
        "cost_usd": facts.cost_usd,
        "usage_present": facts.usage_present,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def aggregate(rows: list[dict]) -> dict:
    achieved_known = [r for r in rows if r.get("achieved") is not None]
    delivered_known = [r for r in rows if r.get("delivered") is not None]
    n = len(rows)
    achieved = sum(1 for r in achieved_known if r.get("achieved"))
    delivered = sum(1 for r in delivered_known if r.get("delivered"))
    loops = sum(1 for r in rows if r.get("tool_error_loop"))
    converged = [
        int(r["convergence_turns"])
        for r in rows
        if r.get("convergence_turns") is not None
    ]
    corrections = [int(r.get("correction_messages") or 0) for r in rows]
    turns = [int(r.get("turns") or 0) for r in rows]
    costs = [float(r["cost_usd"]) for r in rows if r.get("cost_usd") is not None]
    buckets = Counter(str(r.get("bucket") or "unavailable") for r in rows)
    return {
        "n": n,
        "n_achieved_known": len(achieved_known),
        "achievement_rate": _rate(achieved, len(achieved_known)),
        "delivered_rate": _rate(delivered, len(delivered_known)),
        "n_delivered_known": len(delivered_known),
        "convergence_mean": round(sum(converged) / len(converged), 3) if converged else None,
        "n_converged": len(converged),
        "correction_mean": round(sum(corrections) / n, 3) if n else None,
        "tool_error_loop_rate": _rate(loops, n),
        "turn_efficiency_mean": round(sum(turns) / n, 3) if n else None,
        "cost_usd_sum": round(sum(costs), 6) if costs else None,
        "n_with_cost": len(costs),
        "buckets": dict(sorted(buckets.items())),
    }


def paired_delta(base: list[dict], candidate: list[dict], key: str) -> dict:
    """Paired difference for a binary or numeric key; unusable pairs are n/a."""
    if len(base) != len(candidate):
        raise ValueError("paired delta needs one candidate row per base row")
    diffs: list[float] = []
    base_vals: list[float] = []
    cand_vals: list[float] = []
    for b, c in zip(base, candidate):
        bv = b.get(key)
        cv = c.get(key)
        if bv is None or cv is None:
            continue
        if key == "delivered":
            bv = bool(bv)
            cv = bool(cv)
        try:
            bv = float(bv)
            cv = float(cv)
        except (TypeError, ValueError):
            continue
        base_vals.append(bv)
        cand_vals.append(cv)
        diffs.append(cv - bv)
    n = len(diffs)
    if n == 0:
        return {
            "key": key,
            "n": 0,
            "base": None,
            "candidate": None,
            "delta": None,
            "ci95": None,
            "effect_size": None,
        }
    mean_delta = sum(diffs) / n
    base_mean = sum(base_vals) / n
    cand_mean = sum(cand_vals) / n
    ci = bootstrap_ci(diffs)
    return {
        "key": key,
        "n": n,
        "base": round(base_mean, 6),
        "candidate": round(cand_mean, 6),
        "delta": round(mean_delta, 6),
        "relative": (
            round(mean_delta / base_mean, 6) if base_mean not in (0.0, None) else None
        ),
        "ci95": ci,
        "effect_size": round(cohens_h(base_mean, cand_mean), 6)
        if key in {"achieved", "delivered"}
        else round(mean_delta, 6),
        "improved": sum(1 for d in diffs if d > 0),
        "regressed": sum(1 for d in diffs if d < 0),
        "unchanged": sum(1 for d in diffs if d == 0),
    }


def bootstrap_ci(diffs: list[float], *, rounds: int = 2000, seed: int = 0) -> list[float] | None:
    if not diffs:
        return None
    rng = random.Random(seed)
    n = len(diffs)
    means = []
    for _ in range(rounds):
        means.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int(0.025 * (rounds - 1))]
    hi = means[int(0.975 * (rounds - 1))]
    return [round(lo, 6), round(hi, 6)]


def cohens_h(p1: float, p2: float) -> float:
    return 2 * math.asin(math.sqrt(max(0.0, min(1.0, p2)))) - 2 * math.asin(
        math.sqrt(max(0.0, min(1.0, p1)))
    )


def anomaly_distribution(rows: list[dict]) -> dict:
    counts = Counter(str(r.get("bucket") or "unavailable") for r in rows)
    total = sum(counts.values()) or 1
    return {
        key: {"n": count, "share": round(count / total, 4)}
        for key, count in sorted(counts.items())
    }


def domain_breakdown(base: list[dict], candidate: list[dict], domains: list[str]) -> dict:
    out: dict[str, dict] = {}
    seen: set[str] = set()
    for domain in domains:
        b = [row for row in base if row.get("domain") == domain]
        c = [row for row in candidate if row.get("domain") == domain]
        if not b and not c:
            continue
        seen.add(domain)
        out[domain] = {
            "base": aggregate(b),
            "candidate": aggregate(c),
            "achieved_delta": paired_delta(b, c, "achieved"),
            "delivered_delta": paired_delta(b, c, "delivered"),
        }
    return out


__all__ = [
    "RunFacts",
    "aggregate",
    "anomaly_distribution",
    "bootstrap_ci",
    "bucket_of",
    "cohens_h",
    "compute_metrics",
    "domain_breakdown",
    "paired_delta",
    "tool_error_loops",
]
