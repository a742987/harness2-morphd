"""The two-gate budget: static refusal, then stop before the next call."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import config, pricing, store


class BudgetError(RuntimeError):
    pass


class BudgetExceeded(BudgetError):
    def __init__(self, message: str, snapshot: dict):
        super().__init__(message)
        self.snapshot = snapshot


@dataclass(frozen=True)
class Limits:
    usd: float | None = None
    calls: int | None = None
    label: str = "run"


@dataclass(frozen=True)
class PlanRow:
    runtime: str
    tasks: int
    rollout_calls: int
    session_calls: int
    total_calls: int
    estimated_usd: float
    budget_usd: float | None
    budget_calls: int | None
    proposer_channel: str

    def to_dict(self) -> dict:
        return {
            "runtime": self.runtime,
            "tasks": self.tasks,
            "rollout_calls": self.rollout_calls,
            "session_calls": self.session_calls,
            "total_calls": self.total_calls,
            "estimated_usd": round(self.estimated_usd, 4),
            "budget_usd": self.budget_usd,
            "budget_calls": self.budget_calls,
            "proposer_channel": self.proposer_channel,
        }


def estimate_run(
    *,
    runtime: str,
    turn_counts: list[int],
    k: int,
    price: pricing.Pricing,
    limits: Limits,
    proposer_channel: str,
) -> PlanRow:
    tasks = len(turn_counts)
    rollouts = 4 * int(k) * sum(max(1, int(t)) for t in turn_counts)
    sessions = 3 * int(k) * tasks
    total = rollouts + sessions
    usd = (
        rollouts * price.estimate_call_usd(runtime)
        + sessions * price.estimate_session_usd(proposer_channel)
    )
    return PlanRow(
        runtime=runtime,
        tasks=tasks,
        rollout_calls=rollouts,
        session_calls=sessions,
        total_calls=total,
        estimated_usd=usd,
        budget_usd=limits.usd,
        budget_calls=limits.calls,
        proposer_channel=proposer_channel,
    )


def estimate_evaluation(
    *,
    runtime: str,
    turn_counts: list[int],
    price: pricing.Pricing,
    limits: Limits,
) -> PlanRow:
    tasks = len(turn_counts)
    calls = 2 * sum(max(1, int(t)) for t in turn_counts)
    usd = calls * price.estimate_call_usd(runtime)
    return PlanRow(
        runtime=runtime,
        tasks=tasks,
        rollout_calls=calls,
        session_calls=0,
        total_calls=calls,
        estimated_usd=usd,
        budget_usd=limits.usd,
        budget_calls=limits.calls,
        proposer_channel="",
    )


def check_plan(rows: list[PlanRow]) -> None:
    problems: list[str] = []
    for row in rows:
        if row.budget_usd is not None and row.estimated_usd > row.budget_usd:
            problems.append(
                f"{row.runtime}: estimated ${row.estimated_usd:.4f} exceeds "
                f"--budget-usd {row.budget_usd:.4f}"
            )
        if row.budget_calls is not None and row.total_calls > row.budget_calls:
            problems.append(
                f"{row.runtime}: estimated {row.total_calls} calls exceeds "
                f"--budget-calls {row.budget_calls}"
            )
    if problems:
        raise BudgetError(
            "refusing to start: " + "; ".join(problems) + ". No model call was made."
        )


def format_plan(rows: list[PlanRow], *, runtime_order: list[str]) -> str:
    lines = [
        "runtime      tasks  rollout-calls  session-calls  total-calls  est-USD  budget-USD  budget-calls",
        "-----------  -----  -------------  -------------  -----------  -------  ----------  ------------",
    ]
    for runtime in runtime_order:
        for row in rows:
            if row.runtime != runtime:
                continue
            lines.append(
                f"{row.runtime:<11}  {row.tasks:>5}  {row.rollout_calls:>13}  "
                f"{row.session_calls:>13}  {row.total_calls:>11}  "
                f"{row.estimated_usd:>7.4f}  "
                f"{(f'{row.budget_usd:.2f}' if row.budget_usd is not None else '-'):>10}  "
                f"{(str(row.budget_calls) if row.budget_calls is not None else '-'):>12}"
            )
    return "\n".join(lines)


@dataclass
class BudgetGuard:
    limits: Limits
    journal: Path | None = None
    pricing_snapshot: dict = field(default_factory=dict)
    calls: int = 0
    usd: float = 0.0
    estimated_calls: int = 0
    exceeded: bool = False
    messages: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        self._load_journal()

    def _load_journal(self) -> None:
        if self.journal is None:
            return
        for row in store.read_jsonl(Path(self.journal)):
            event = row.get("event")
            if event == "reserve":
                self.calls += 1
            elif event == "release":
                self.calls = max(0, self.calls - 1)
            elif event == "charge":
                try:
                    self.usd += float(row.get("usd") or 0.0)
                except (TypeError, ValueError):
                    pass
                if row.get("estimated"):
                    self.estimated_calls += 1

    def _append(self, payload: dict) -> None:
        if self.journal is None:
            return
        store.append_jsonl(
            Path(self.journal),
            {"at": store.utc_now(), **payload, "pricing": self.pricing_snapshot},
        )

    def _stop(self, reason: str) -> None:
        self.exceeded = True
        self.messages.append(reason)
        snapshot = self.snapshot()
        raise BudgetExceeded(f"budget exceeded: {reason}", snapshot)

    def reserve(self, *, kind: str, label: str) -> None:
        with self._lock:
            if self.exceeded:
                self._stop(self.messages[-1] if self.messages else "already stopped")
            if self.limits.calls is not None and self.calls >= int(self.limits.calls):
                self._stop(
                    f"call limit {self.limits.calls} reached before {kind} {label!r}"
                )
            if self.limits.usd is not None and self.usd >= float(self.limits.usd):
                self._stop(
                    f"spend ${self.usd:.4f} reached limit ${self.limits.usd:.2f} "
                    f"before {kind} {label!r}"
                )
            self.calls += 1
            self._append({"event": "reserve", "kind": kind, "label": label})

    def release(self, *, kind: str, label: str) -> None:
        """Undo a reservation for a cached call that never reached a model."""
        with self._lock:
            if self.calls > 0:
                self.calls -= 1
            self._append({"event": "release", "kind": kind, "label": label})

    def charge(self, usd: float | None, *, kind: str, label: str, estimated: bool = False) -> None:
        value = max(0.0, float(usd or 0.0))
        with self._lock:
            self.usd += value
            if estimated:
                self.estimated_calls += 1
            self._append(
                {
                    "event": "charge",
                    "kind": kind,
                    "label": label,
                    "usd": round(value, 6),
                    "estimated": bool(estimated),
                }
            )
            if self.limits.usd is not None and self.usd >= float(self.limits.usd):
                self.exceeded = True
                self.messages.append(
                    f"spent ${self.usd:.4f}, at or over --budget-usd {self.limits.usd:.2f}"
                )

    def snapshot(self) -> dict:
        return {
            "label": self.limits.label,
            "calls": self.calls,
            "usd": round(self.usd, 6),
            "estimated_calls": self.estimated_calls,
            "limits": {"usd": self.limits.usd, "calls": self.limits.calls},
            "exceeded": self.exceeded,
            "messages": list(self.messages),
        }


@dataclass
class RunBudget:
    settings: config.Settings
    limits_by_runtime: dict[str, Limits]
    shared: bool = False
    namespace: str = "run"
    pricing_snapshot: dict = field(default_factory=dict)
    _guards: dict[str, BudgetGuard] = field(default_factory=dict)
    _shared_guard: BudgetGuard | None = None

    def __post_init__(self) -> None:
        if self.shared:
            usd_values = [limit.usd for limit in self.limits_by_runtime.values() if limit.usd is not None]
            call_values = [
                limit.calls
                for limit in self.limits_by_runtime.values()
                if limit.calls is not None
            ]
            limits = Limits(
                usd=sum(usd_values) if usd_values else None,
                calls=sum(call_values) if call_values else None,
                label="shared",
            )
            journal = self.settings.runs_root / "_budget" / f"{store.safe_slug(self.namespace)}.jsonl"
            self._shared_guard = BudgetGuard(
                limits=limits, journal=journal, pricing_snapshot=self.pricing_snapshot
            )

    def guard_for(self, runtime: str) -> BudgetGuard:
        if self.shared and self._shared_guard is not None:
            return self._shared_guard
        if runtime not in self._guards:
            limits = self.limits_by_runtime.get(runtime, Limits(label=runtime))
            journal = (
                self.settings.runs_root
                / runtime
                / "_budget"
                / f"{store.safe_slug(self.namespace)}.jsonl"
            )
            self._guards[runtime] = BudgetGuard(
                limits=limits, journal=journal, pricing_snapshot=self.pricing_snapshot
            )
        return self._guards[runtime]

    def snapshots(self) -> dict[str, dict]:
        out = {runtime: guard.snapshot() for runtime, guard in self._guards.items()}
        if self._shared_guard is not None:
            out["shared"] = self._shared_guard.snapshot()
        return out

    @property
    def exceeded(self) -> bool:
        if self._shared_guard is not None and self._shared_guard.exceeded:
            return True
        return any(g.exceeded for g in self._guards.values())
