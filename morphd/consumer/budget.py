"""Consumer three-tier budget: monthly, per-session and per-analysis-job."""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from .. import store as engine_store
from .errors import ConsumerError
from .store import ConsumerStore


def _month_key(now: datetime) -> str:
    return now.astimezone(timezone.utc).strftime("%Y-%m")


@dataclass(frozen=True)
class BudgetSnapshot:
    monthly_limit: float
    session_limit: float
    job_limit: float
    monthly_spent: float
    session_spent: float
    job_spent: float
    monthly_committed: float
    session_committed: float
    job_committed: float
    alert_ratio: float
    alert: bool
    blocked: bool
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "monthly": {
                "limit": self.monthly_limit,
                "spent": round(self.monthly_spent, 6),
                "committed": round(self.monthly_committed, 6),
                "remaining": round(max(0.0, self.monthly_limit - self.monthly_spent), 6),
            },
            "session": {
                "limit": self.session_limit,
                "spent": round(self.session_spent, 6),
                "committed": round(self.session_committed, 6),
                "remaining": round(max(0.0, self.session_limit - self.session_spent), 6),
            },
            "job": {
                "limit": self.job_limit,
                "spent": round(self.job_spent, 6),
                "committed": round(self.job_committed, 6),
                "remaining": round(max(0.0, self.job_limit - self.job_spent), 6),
            },
            "alert_ratio": self.alert_ratio,
            "alert": self.alert,
            "blocked": self.blocked,
            "reason": self.reason,
        }


class ConsumerBudget:
    """Append-only journal with reserve/release/charge events.

    Reservations count as committed spend so concurrent jobs cannot oversubscribe the same
    monthly budget. Charges carry the actual (or estimated) provider cost.
    """

    def __init__(self, store: ConsumerStore):
        self.store = store
        self.journal = store.budget_journal
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_journal()

    def _ensure_journal(self) -> None:
        if not self.journal.exists():
            engine_store.atomic_write_text(self.journal, "")

    def events(self) -> list[dict]:
        return engine_store.read_jsonl(self.journal)

    def _month_events(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        prefix = _month_key(now)
        out = []
        for event in self.events():
            at = str(event.get("at", ""))
            if at[:7] == prefix:
                out.append(event)
        return out

    def _spent(self, events: list[dict], *, session_id=None, job_id=None) -> float:
        total = 0.0
        for event in events:
            if event.get("event") != "charge":
                continue
            if session_id is not None and event.get("session_id") != session_id:
                continue
            if job_id is not None and event.get("job_id") != job_id:
                continue
            try:
                total += float(event.get("usd", 0.0))
            except (TypeError, ValueError):
                continue
        return total

    def _committed(self, events: list[dict], *, session_id=None, job_id=None) -> float:
        open_reserves: dict[str, float] = {}
        for event in events:
            rid = event.get("reservation")
            if not rid:
                continue
            if session_id is not None and event.get("session_id") != session_id:
                continue
            if job_id is not None and event.get("job_id") != job_id:
                continue
            try:
                usd = float(event.get("usd", 0.0))
            except (TypeError, ValueError):
                usd = 0.0
            if event.get("event") == "reserve":
                open_reserves[rid] = open_reserves.get(rid, 0.0) + usd
            elif event.get("event") in ("release", "charge"):
                open_reserves.pop(rid, None)
        return sum(open_reserves.values())

    def session_spent(self, session_id: str) -> float:
        return self._spent(self._month_events(), session_id=session_id)

    def job_spent(self, job_id: str) -> float:
        return self._spent(self._month_events(), job_id=job_id)

    def monthly_spent(self) -> float:
        return self._spent(self._month_events())

    def snapshot(
        self,
        *,
        monthly_limit: float,
        session_limit: float,
        job_limit: float,
        session_id: str | None = None,
        job_id: str | None = None,
        alert_ratio: float = 0.8,
    ) -> BudgetSnapshot:
        events = self._month_events()
        monthly = self._spent(events)
        session = self._spent(events, session_id=session_id) if session_id else 0.0
        job = self._spent(events, job_id=job_id) if job_id else 0.0
        monthly_committed = self._committed(events)
        session_committed = self._committed(events, session_id=session_id) if session_id else 0.0
        job_committed = self._committed(events, job_id=job_id) if job_id else 0.0
        blocked = False
        reason = ""
        if monthly >= monthly_limit:
            blocked, reason = True, "monthly"
        elif session_id and session >= session_limit:
            blocked, reason = True, "session"
        elif job_id and job >= job_limit:
            blocked, reason = True, "job"
        alert = monthly_limit > 0 and monthly >= monthly_limit * alert_ratio
        return BudgetSnapshot(
            monthly_limit=monthly_limit,
            session_limit=session_limit,
            job_limit=job_limit,
            monthly_spent=monthly,
            session_spent=session,
            job_spent=job,
            monthly_committed=monthly_committed,
            session_committed=session_committed,
            job_committed=job_committed,
            alert_ratio=alert_ratio,
            alert=alert,
            blocked=blocked,
            reason=reason,
        )

    def precheck(
        self,
        estimate_usd: float | None,
        *,
        monthly_limit: float,
        session_limit: float,
        job_limit: float,
        session_id: str | None,
        job_id: str | None,
        alert_ratio: float = 0.8,
    ) -> BudgetSnapshot:
        if estimate_usd is None:
            raise ConsumerError(
                code="BUDGET_UNKNOWN_PRICE",
                message="这个模型没有已确认的价格，harness2-morphd 不会按 0 计费。",
                action="在模型页填写估算价格，或明确确认按未知成本运行。",
            )
        if not math.isfinite(float(estimate_usd)) or estimate_usd < 0:
            raise ConsumerError(
                code="BUDGET_UNKNOWN_PRICE",
                message="成本预估无效。",
                action="检查模型价格设置。",
            )
        snap = self.snapshot(
            monthly_limit=monthly_limit,
            session_limit=session_limit,
            job_limit=job_limit,
            session_id=session_id,
            job_id=job_id,
            alert_ratio=alert_ratio,
        )
        if snap.blocked:
            raise ConsumerError(
                code="BUDGET_EXCEEDED",
                message={
                    "monthly": "本月自动学习预算已用完，不会再自动分析。",
                    "session": "这个会话的预算已用完，不会分析。",
                    "job": "这次分析的预算已用完。",
                }[snap.reason],
                action="调高预算，或等待下个月。",
                details={"reason": snap.reason, "budget": snap.to_dict()},
            )
        remaining_month = monthly_limit - snap.monthly_spent - snap.monthly_committed
        if estimate_usd > max(0.0, remaining_month) + 1e-9:
            raise ConsumerError(
                code="BUDGET_EXCEEDED",
                message="按最坏情况估算，本次分析会超过本月预算，所以没有启动。",
                action="调高月度预算，或选择更便宜的模型。",
                details={"estimate_usd": estimate_usd, "remaining_usd": remaining_month},
            )
        if session_id:
            remaining_session = session_limit - snap.session_spent - snap.session_committed
            if estimate_usd > max(0.0, remaining_session) + 1e-9:
                raise ConsumerError(
                    code="BUDGET_EXCEEDED",
                    message="按最坏情况估算，本次分析会超过这个会话的预算。",
                    action="关闭本次分析，或调高单会话预算。",
                )
        remaining_job = job_limit - snap.job_spent - snap.job_committed
        if estimate_usd > max(0.0, remaining_job) + 1e-9:
            raise ConsumerError(
                code="BUDGET_EXCEEDED",
                message="按最坏情况估算，本次分析会超过单次分析预算。",
                action="调高单次分析预算，或选择更便宜的模型。",
            )
        return snap

    def reserve(
        self,
        usd: float,
        *,
        kind: str,
        label: str,
        session_id: str | None = None,
        job_id: str | None = None,
        project_id: str | None = None,
        estimated: bool = True,
    ) -> str:
        reservation = uuid.uuid4().hex
        self._append(
            {
                "event": "reserve",
                "reservation": reservation,
                "kind": kind,
                "label": label,
                "usd": round(float(usd), 6),
                "estimated": estimated,
                "session_id": session_id,
                "job_id": job_id,
                "project_id": project_id,
            }
        )
        return reservation

    def release(self, reservation: str, *, reason: str = "") -> None:
        self._append(
            {
                "event": "release",
                "reservation": reservation,
                "usd": 0.0,
                "reason": reason,
            }
        )

    def charge(
        self,
        usd: float | None,
        *,
        kind: str,
        label: str,
        reservation: str | None = None,
        session_id: str | None = None,
        job_id: str | None = None,
        project_id: str | None = None,
        estimated: bool = False,
        usage: dict | None = None,
    ) -> float:
        if usd is None:
            raise ConsumerError(
                code="BUDGET_UNKNOWN_PRICE",
                message="模型返回了无法计价的用量。",
                action="补充模型估算价格后重试。",
            )
        value = max(0.0, float(usd))
        self._append(
            {
                "event": "charge",
                "reservation": reservation,
                "kind": kind,
                "label": label,
                "usd": round(value, 6),
                "estimated": bool(estimated),
                "session_id": session_id,
                "job_id": job_id,
                "project_id": project_id,
                "usage": usage or {},
            }
        )
        return value

    def _append(self, payload: dict) -> None:
        with self.store.lock("budget"):
            event = {
                "at": engine_store.utc_now(),
                "ts": time.time(),
                **payload,
            }
            engine_store.append_jsonl(self.journal, event)

    def release_stale(self, *, max_age_seconds: float = 6 * 3600) -> int:
        """Release reservations left open by a crashed/stopped process."""
        now = time.time()
        open_reserves: dict[str, float] = {}
        for event in self.events():
            rid = event.get("reservation")
            if not rid:
                continue
            if event.get("event") == "reserve":
                try:
                    open_reserves[rid] = float(event.get("ts") or 0.0)
                except (TypeError, ValueError):
                    open_reserves[rid] = 0.0
            elif event.get("event") in ("release", "charge"):
                open_reserves.pop(rid, None)
        released = 0
        for rid, stamp in open_reserves.items():
            if now - stamp >= max_age_seconds:
                self.release(rid, reason="stale")
                released += 1
        return released

    def monthly_usage(self) -> list[dict]:
        out: dict[tuple[str, str], float] = {}
        for event in self._month_events():
            if event.get("event") != "charge":
                continue
            key = (str(event.get("kind", "unknown")), str(event.get("label", "")))
            try:
                out[key] = out.get(key, 0.0) + float(event.get("usd", 0.0))
            except (TypeError, ValueError):
                continue
        return [
            {"kind": kind, "label": label, "usd": round(usd, 6)}
            for (kind, label), usd in sorted(out.items(), key=lambda kv: -kv[1])
        ]
