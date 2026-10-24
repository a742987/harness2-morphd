"""Quiet hours, focus, pause/vacation, weekly caps and feedback-driven down-ranking.

Analysis and presentation are deliberately decoupled (PRD §9.8): sessions always enqueue
unless explicitly incognito/paused, while this module decides whether a suggestion may be
surfaced proactively. Everything suppressed remains in the local inbox.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from .settings import deep_merge
from .store import ConsumerStore

DEFAULT_STATE: dict[str, Any] = {
    "version": 1,
    "paused_until": None,
    "vacation": False,
    "vacation_skipped": 0,
    "last_vacation_skipped": 0,
    "incognito_next": False,
    "incognito_next_at": None,
    "incognito_sessions": [],
    "incognito_done": [],
    "active_sessions": [],
    "weekly": {},
    "feedback": {},
    "alerts": {},
    "last_digest": None,
    "tray_processed": 0,
}


def parse_now(now: datetime | None = None) -> datetime:
    return (now or datetime.now().astimezone()).astimezone()


def _parse_hhmm(value: str) -> tuple[int, int]:
    hour, minute = value.split(":", 1)
    return int(hour), int(minute)


class AttentionManager:
    def __init__(self, store: ConsumerStore):
        self.store = store

    # --- runtime state --------------------------------------------------------
    def state(self) -> dict:
        raw = self.store.read_json(self.store.runtime_state_path, default={})
        return deep_merge(DEFAULT_STATE, raw if isinstance(raw, dict) else {})

    def _save(self, state: dict) -> dict:
        with self.store.lock("state"):
            self.store.write_json(self.store.runtime_state_path, state)
        return state

    def update(self, **changes) -> dict:
        state = self.state()
        state.update(changes)
        return self._save(state)

    # --- pause / vacation -----------------------------------------------------
    def pause(self, until: str | None, *, now: datetime | None = None) -> dict:
        now = parse_now(now)
        if not until or until in ("until_resume", "forever"):
            state = self.state()
            state["paused_until"] = None
            state["paused_forever"] = True
            return self._save(state)
        durations = {"1h": timedelta(hours=1), "1d": timedelta(days=1), "1w": timedelta(weeks=1)}
        if until in durations:
            target = now + durations[until]
        else:
            try:
                target = datetime.fromisoformat(until.replace("Z", "+00:00"))
            except ValueError:
                target = now + timedelta(days=1)
        state = self.state()
        state["paused_until"] = target.astimezone(timezone.utc).isoformat(timespec="seconds")
        state["paused_forever"] = False
        self.store.append_log("learning_paused", until=state["paused_until"])
        return self._save(state)

    def resume(self) -> dict:
        state = self.state()
        state["paused_until"] = None
        state["paused_forever"] = False
        was_vacation = bool(state.get("vacation"))
        state["last_vacation_skipped"] = int(state.get("vacation_skipped", 0) or 0) if was_vacation else 0
        state["vacation_skipped"] = 0
        state["vacation"] = False
        self.store.append_log("learning_resumed", skipped=state["last_vacation_skipped"])
        return self._save(state)

    def note_vacation_skip(self) -> int:
        state = self.state()
        if not state.get("vacation"):
            return 0
        state["vacation_skipped"] = int(state.get("vacation_skipped", 0) or 0) + 1
        self._save(state)
        return state["vacation_skipped"]

    def set_vacation(self, active: bool) -> dict:
        state = self.state()
        state["vacation"] = bool(active)
        if active:
            state["vacation_started"] = parse_now().isoformat(timespec="seconds")
            state["paused_until"] = None
            state["paused_forever"] = False
        self.store.append_log("vacation", active=bool(active))
        return self._save(state)

    def is_paused(self, *, now: datetime | None = None) -> bool:
        state = self.state()
        if state.get("vacation"):
            return True
        if state.get("paused_forever"):
            return True
        raw = state.get("paused_until")
        if not raw:
            return False
        try:
            until = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return False
        return parse_now(now) < until.astimezone(parse_now(now).tzinfo)

    # --- incognito / focus ----------------------------------------------------
    def set_incognito(self, active: bool, session_id: str | None = None) -> dict:
        state = self.state()
        sessions = list(state.get("incognito_sessions", []))
        if active:
            if session_id:
                if session_id not in sessions:
                    sessions.append(session_id)
            else:
                state["incognito_next"] = True
                state["incognito_next_at"] = parse_now().isoformat(timespec="seconds")
        else:
            if session_id:
                sessions = [s for s in sessions if s != session_id]
            else:
                state["incognito_next"] = False
                state["incognito_next_at"] = None
                sessions = []
        state["incognito_sessions"] = sessions
        self.store.append_log("incognito", active=bool(active), session_id=session_id)
        return self._save(state)

    def is_incognito(self, session_id: str | None = None) -> bool:
        state = self.state()
        if session_id and session_id in state.get("incognito_sessions", []):
            return True
        if session_id is None:
            return bool(state.get("incognito_next"))
        return False

    def consume_incognito(self, session_id: str) -> bool:
        state = self.state()
        sessions = list(state.get("incognito_sessions", []))
        if session_id in sessions:
            sessions.remove(session_id)
            state["incognito_sessions"] = sessions
            done = list(state.get("incognito_done", []))
            if session_id not in done:
                done.append(session_id)
            state["incognito_done"] = done[-500:]
            self._save(state)
            return True
        if state.get("incognito_next"):
            state["incognito_next"] = False
            done = list(state.get("incognito_done", []))
            if session_id not in done:
                done.append(session_id)
            state["incognito_done"] = done[-500:]
            self._save(state)
            return True
        return False

    def was_incognito(self, session_id: str) -> bool:
        return session_id in set(self.state().get("incognito_done", []))

    def session_started(self, session_id: str, host: str) -> None:
        state = self.state()
        active = [item for item in state.get("active_sessions", []) if item.get("session_id") != session_id]
        active.append(
            {"session_id": session_id, "host": host, "started_at": parse_now().isoformat(timespec="seconds")}
        )
        state["active_sessions"] = active
        if state.get("incognito_next"):
            sessions = list(state.get("incognito_sessions", []))
            if session_id not in sessions:
                sessions.append(session_id)
            state["incognito_sessions"] = sessions
            state["incognito_next"] = False
            state["incognito_next_at"] = None
        self._save(state)

    def apply_pending_incognito(self, session_id: str, started_at: str) -> bool:
        """Apply a pre-set "next session incognito" only to sessions that started after it.

        This covers hosts whose session-start signal is unavailable; a session that was
        already running when the user toggled incognito is not retroactively captured (its
        already-recorded content is not re-sent) but the next one is.
        """
        state = self.state()
        if not state.get("incognito_next"):
            return False
        try:
            started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            marker = datetime.fromisoformat(str(state.get("incognito_next_at") or ""))
        except ValueError:
            return False
        if started < marker:
            return False
        sessions = list(state.get("incognito_sessions", []))
        if session_id not in sessions:
            sessions.append(session_id)
        state["incognito_sessions"] = sessions
        state["incognito_next"] = False
        state["incognito_next_at"] = None
        self._save(state)
        return True

    def session_ended(self, session_id: str) -> None:
        state = self.state()
        state["active_sessions"] = [
            item for item in state.get("active_sessions", []) if item.get("session_id") != session_id
        ]
        self._save(state)

    def clear_host_sessions(self, host: str) -> None:
        state = self.state()
        state["active_sessions"] = [
            item for item in state.get("active_sessions", []) if item.get("host") != host
        ]
        self._save(state)

    def is_focus_active(self) -> bool:
        return bool(self.state().get("active_sessions"))

    # --- cadence --------------------------------------------------------------
    @staticmethod
    def _in_quiet_hours(quiet: list[str] | None, now: datetime) -> bool:
        if not quiet:
            return False
        try:
            start = now.replace(
                hour=_parse_hhmm(quiet[0])[0], minute=_parse_hhmm(quiet[0])[1], second=0, microsecond=0
            )
            end = now.replace(
                hour=_parse_hhmm(quiet[1])[0], minute=_parse_hhmm(quiet[1])[1], second=0, microsecond=0
            )
        except (ValueError, IndexError):
            return False
        if start == end:
            return False
        if start < end:
            return start <= now < end
        return now >= start or now < end

    @staticmethod
    def week_key(now: datetime) -> str:
        iso = now.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    def weekly_count(self, project_id: str, *, now: datetime | None = None) -> int:
        state = self.state()
        week = state.get("weekly", {}).get(self.week_key(parse_now(now)), {})
        try:
            return int(week.get(project_id, 0))
        except (TypeError, ValueError):
            return 0

    def _increment_weekly(self, project_id: str, *, now: datetime | None = None) -> int:
        state = self.state()
        key = self.week_key(parse_now(now))
        weeks = state.setdefault("weekly", {})
        week = weeks.setdefault(key, {})
        week[project_id] = int(week.get(project_id, 0)) + 1
        # Keep only the current and adjacent week to keep state bounded.
        for old in sorted(weeks)[:-2]:
            weeks.pop(old, None)
        self._save(state)
        return week[project_id]

    def feedback_stats(self, project_id: str) -> dict:
        return dict(self.state().get("feedback", {}).get(project_id, {}))

    def feedback_overview(self) -> dict:
        """Projects that asked for a reason after repeated ignores/negative feedback."""
        prompts = []
        for project_id, stats in sorted(self.state().get("feedback", {}).items()):
            if not isinstance(stats, dict) or not stats.get("ask_reason"):
                continue
            counts = stats.get("counts") or {}
            prompts.append(
                {
                    "project_id": project_id,
                    "downranked": bool(stats.get("downranked")),
                    "counts": {
                        key: value
                        for key, value in counts.items()
                        if isinstance(value, int) and not isinstance(value, bool)
                    },
                    "quiet_until": stats.get("quiet_until"),
                }
            )
        return {"prompts": prompts}

    def respond_feedback(self, project_id: str, reason: str) -> dict:
        """Record the reason chosen in the prompt, then resume normal presentation."""
        if reason != "resume":
            self.record_feedback(project_id, reason)
        return self.reset_downrank(project_id, clear_quiet=reason == "resume")

    def record_feedback(self, project_id: str, reason: str, *, candidate_kind: str = "") -> dict:
        state = self.state()
        projects = state.setdefault("feedback", {})
        stats = dict(projects.get(project_id, {}))
        counts = dict(stats.get("counts", {}))
        counts[reason] = int(counts.get(reason, 0)) + 1
        if reason == "ignore":
            stats["consecutive_ignores"] = int(stats.get("consecutive_ignores", 0)) + 1
        elif reason == "useful":
            stats["consecutive_ignores"] = 0
        elif reason == "applied":
            stats["consecutive_ignores"] = 0
        if reason == "not_this_kind" and candidate_kind:
            kinds = set(stats.get("suppressed_kinds", []))
            kinds.add(candidate_kind)
            stats["suppressed_kinds"] = sorted(kinds)
        if reason == "intrusive":
            stats["quiet_until"] = (parse_now() + timedelta(days=7)).isoformat(timespec="seconds")
        stats["counts"] = counts
        stats["updated_at"] = parse_now().isoformat(timespec="seconds")
        if int(stats.get("consecutive_ignores", 0)) >= 3 or int(counts.get("not_useful", 0)) >= 2:
            stats["downranked"] = True
            stats["ask_reason"] = True
        projects[project_id] = stats
        state["feedback"] = projects
        self._save(state)
        self.store.append_log("feedback", project_id=project_id, reason=reason)
        return stats

    def reset_downrank(self, project_id: str, *, clear_quiet: bool = False) -> dict:
        state = self.state()
        stats = dict(state.get("feedback", {}).get(project_id, {}))
        stats["downranked"] = False
        stats["ask_reason"] = False
        stats["consecutive_ignores"] = 0
        if clear_quiet:
            stats.pop("quiet_until", None)
        state.setdefault("feedback", {})[project_id] = stats
        self._save(state)
        return stats

    def presentation(self, *, settings: dict, project_id: str, confidence: str = "normal",
                     kind: str = "instruction", now: datetime | None = None) -> dict:
        """Return {present, reason} without side effects. ``claim`` records a weekly slot."""
        now = parse_now(now)
        if not settings.get("enabled", True):
            return {"present": False, "reason": "disabled"}
        if self.is_paused(now=now):
            return {"present": False, "reason": "paused"}
        if self.is_incognito(None):
            return {"present": False, "reason": "incognito"}
        cadence = settings.get("cadence", {})
        if cadence.get("focus_mode", True) and self.is_focus_active():
            return {"present": False, "reason": "focus"}
        if self._in_quiet_hours(cadence.get("quiet_hours"), now):
            return {"present": False, "reason": "quiet_hours"}
        stats = self.feedback_stats(project_id)
        if stats.get("downranked"):
            return {"present": False, "reason": "downranked"}
        quiet_until = stats.get("quiet_until")
        if quiet_until:
            try:
                if now < datetime.fromisoformat(str(quiet_until).replace("Z", "+00:00")).astimezone(now.tzinfo):
                    return {"present": False, "reason": "feedback_quiet"}
            except ValueError:
                pass
        if kind in set(stats.get("suppressed_kinds", [])):
            return {"present": False, "reason": "kind_suppressed"}
        cap = int(cadence.get("weekly_cap", 2) or 0)
        count = self.weekly_count(project_id, now=now)
        if count >= cap:
            return {"present": False, "reason": "weekly_cap"}
        digest = cadence.get("digest", "weekly")
        if digest == "only_inbox":
            return {"present": False, "reason": "inbox_only"}
        if confidence != "high" and digest in ("daily", "weekly"):
            return {"present": False, "reason": "digest"}
        return {"present": True, "reason": "high_confidence" if confidence == "high" else "immediate"}

    def claim_presentation(self, project_id: str, *, now: datetime | None = None) -> int:
        return self._increment_weekly(project_id, now=now)

    def reset_week(self, project_id: str, *, now: datetime | None = None) -> None:
        state = self.state()
        state.setdefault("weekly", {}).setdefault(self.week_key(parse_now(now)), {})[project_id] = 0
        self._save(state)
