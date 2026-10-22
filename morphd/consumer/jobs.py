"""In-host analysis queue: idempotent enqueue, checkpoints, cancel and resume."""

from __future__ import annotations

import hashlib
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path

from .analyzer import Analyzer
from .attention import AttentionManager
from .budget import ConsumerBudget
from .errors import ConsumerError
from .session_import import SessionContext, save_session
from .settings import ProjectManager, SettingsManager
from .store import ConsumerStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_id(session: dict) -> str:
    key = f"{session.get('host')}\0{session.get('id')}\0{session.get('ended_at')}"
    return "j_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


class JobRunner:
    """A single worker owned by the host lifecycle.

    The queue is persisted in job JSON so a host exit cannot lose a session. Workers never
    outlive ``stop()`` and never retry in the background after the host is gone.
    """

    def __init__(self, store: ConsumerStore):
        self.store = store
        self.settings = SettingsManager(store)
        self.projects = ProjectManager(store)
        self.attention = AttentionManager(store)
        self.budget = ConsumerBudget(store)
        self.analyzer = Analyzer(store)
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stop = threading.Event()
        self._cancel: set[str] = set()
        self._thread: threading.Thread | None = None
        self._running_id: str | None = None
        self._lock = threading.Lock()

    # --- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._recover()
        self._thread = threading.Thread(target=self._work, name="morphd-jobs", daemon=True)
        self._thread.start()
        self.store.append_log("job_runner_started")

    def stop(self, *, timeout: float = 10.0) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        with self._lock:
            running = self._running_id
        if running:
            self._mark_paused(running, reason="host_exit")
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        self.store.append_log("job_runner_stopped")

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def current_job(self) -> str | None:
        with self._lock:
            return self._running_id

    def _stop_requested(self) -> bool:
        with self._lock:
            current = self._running_id
        return self._stop.is_set() or (current is not None and current in self._cancel)

    # --- enqueue --------------------------------------------------------------
    def _recover(self) -> None:
        resumable = []
        for job in self.store.list_records("jobs"):
            state = job.get("state")
            if state == "running":
                job["state"] = "paused"
                job["updated_at"] = _now()
                job.setdefault("checkpoint", {})["stage"] = job.get("checkpoint", {}).get("stage", "queued")
                self.store.write_record("jobs", str(job["id"]), job)
                state = "paused"
            if state in ("queued", "paused"):
                if state == "paused":
                    self._release_reservation(job)
                    job["checkpoint"] = {
                        key: value
                        for key, value in (job.get("checkpoint") or {}).items()
                        if key != "reservation"
                    }
                resumable.append(job)
        for job in sorted(resumable, key=lambda item: item.get("created_at", "")):
            self._queue.put(str(job["id"]))
        if resumable:
            self.store.append_log("jobs_recovered", count=len(resumable))

    def enqueue(self, session: dict, *, force: bool = False) -> dict | None:
        existing = [
            job for job in self.store.list_records("jobs") if job.get("session_id") == session.get("id")
        ]
        if existing:
            return existing[0]
        settings = self.settings.load()
        project = self.store.read_record("projects", str(session.get("project_id"))) or {}
        budget = {
            "usd": float((project.get("budget") or {}).get("per_session_usd", settings["budget"]["per_session_usd"])),
            "per_job_usd": float((project.get("budget") or {}).get("per_job_usd", settings["budget"]["per_job_usd"])),
        }
        job_id = _job_id(session)
        job_dir = self.store.home / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        job = {
            "version": 1,
            "id": job_id,
            "session_id": session.get("id"),
            "host": session.get("host"),
            "project_id": session.get("project_id"),
            "project": session.get("project"),
            "state": "queued",
            "created_at": _now(),
            "updated_at": _now(),
            "attempts": 0,
            "budget": budget,
            "model": dict(settings.get("models") or {}),
            "candidate_id": None,
            "checkpoint": {"stage": "queued"},
            "error": None,
            "force": bool(force),
            "dir": str(job_dir),
        }
        self.store.write_record("jobs", job_id, job)
        session["job_id"] = job_id
        session["status"] = "queued"
        self.store.write_record("sessions", str(session["id"]), session)
        self._queue.put(job_id)
        self.store.append_log("job_enqueued", job_id=job_id, session_id=session.get("id"))
        return job

    def handle_session_end(
        self,
        context: SessionContext,
        *,
        source_workspace: Path | None = None,
        manual: bool = False,
    ) -> dict:
        if self.attention.is_incognito(context.session_id) or self.attention.was_incognito(context.session_id):
            self.attention.consume_incognito(context.session_id)
            self.store.append_log("session_incognito", host=context.host, session_id=context.session_id)
            self.attention.session_ended(context.session_id)
            return {"captured": False, "reason": "incognito", "session_id": context.session_id}
        session = save_session(
            self.store,
            context,
            source_workspace=source_workspace or Path(context.workspace),
            session_workspace=self.store.home / "sessions" / context.session_id / "workspace",
        )
        self.attention.session_ended(context.session_id)
        # Update any running trial for this host/project before triage.
        try:
            self.analyzer.candidates.observe_session(session)
        except Exception as exc:
            self.store.append_log("trial_update_failed", session_id=session["id"], error=str(exc))
        settings = self.settings.load()
        project = self.store.read_record("projects", str(session.get("project_id"))) or {}
        blocked_reason = ""
        if not manual:
            if not settings.get("enabled", True):
                blocked_reason = "disabled"
            elif self.attention.is_paused():
                blocked_reason = "paused"
            elif project and not project.get("enabled", True):
                blocked_reason = "project_disabled"
            elif project and project.get("sensitive"):
                blocked_reason = "sensitive_project"
        if blocked_reason:
            session["status"] = blocked_reason
            self.store.write_record("sessions", str(session["id"]), session)
            return {
                "captured": True,
                "session_id": session["id"],
                "queued": False,
                "reason": blocked_reason,
            }
        job = self.enqueue(session, force=manual)
        return {
            "captured": True,
            "session_id": session["id"],
            "job_id": job.get("id") if job else None,
            "queued": True,
        }

    # --- worker ---------------------------------------------------------------
    def _work(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if job_id is None:
                break
            if self._stop.is_set():
                break
            try:
                self._run(job_id)
            except Exception as exc:  # never let the worker die silently
                self.store.append_log("job_worker_error", job_id=job_id, error=str(exc))
        self.store.append_log("job_worker_exit")

    def _run(self, job_id: str) -> None:
        job = self.store.read_record("jobs", job_id)
        if not isinstance(job, dict) or job.get("state") not in ("queued", "paused"):
            return
        if self._stop.is_set() or job_id in self._cancel:
            self._mark_canceled(job)
            return
        session = self.store.read_record("sessions", str(job.get("session_id")))
        if not isinstance(session, dict):
            self._fail(job, ConsumerError("NOT_FOUND", "会话记录不存在。", "重新产生一次会话。"))
            return
        with self._lock:
            self._running_id = job_id
        job["state"] = "running"
        job["attempts"] = int(job.get("attempts", 0) or 0) + 1
        job["started_at"] = job.get("started_at") or _now()
        job["updated_at"] = _now()
        self.store.write_record("jobs", job_id, job)
        session["status"] = "analyzing"
        self.store.write_record("sessions", str(session.get("id")), session)
        try:
            result = self.analyzer.run(
                job,
                session,
                should_stop=self._stop_requested,
                force=bool(job.get("force")),
            )
            if job_id in self._cancel:
                self._mark_canceled(job)
            elif self._stop.is_set():
                self._mark_paused(job_id, reason="host_exit")
            else:
                self._finish(job, session, result)
        except ConsumerError as exc:
            if exc.code == "JOB_CANCELED":
                if self._stop.is_set():
                    self._mark_paused(job_id, reason="host_exit")
                else:
                    self._mark_canceled(job)
            elif exc.code == "BUDGET_EXCEEDED":
                self._fail(job, exc, session=session)
            elif exc.code in ("PROVIDER_AUTH_FAILED", "PROVIDER_NOT_CONFIGURED", "PROVIDER_UNAVAILABLE",
                              "PROVIDER_RATE_LIMITED", "BUDGET_UNKNOWN_PRICE"):
                self._fail(job, exc, session=session)
            elif int(job.get("attempts", 0) or 0) < 2:
                job["state"] = "queued"
                job["error"] = exc.to_dict()
                job["updated_at"] = _now()
                self.store.write_record("jobs", job_id, job)
                self._queue.put(job_id)
            else:
                self._fail(job, exc, session=session)
        except Exception as exc:
            wrapped = ConsumerError("INTERNAL", f"分析失败：{exc}", "查看诊断信息或重试。")
            if int(job.get("attempts", 0) or 0) < 2:
                job["state"] = "queued"
                job["error"] = wrapped.to_dict()
                job["updated_at"] = _now()
                self.store.write_record("jobs", job_id, job)
                self._queue.put(job_id)
            else:
                self._fail(job, wrapped, session=session)
        finally:
            with self._lock:
                self._running_id = None
            if self._stop.is_set():
                current = self.store.read_record("jobs", job_id)
                if isinstance(current, dict) and current.get("state") in ("running", "queued"):
                    self._mark_paused(job_id, reason="host_exit")

    def _finish(self, job: dict, session: dict, result: dict) -> None:
        if self._stop.is_set():
            self._mark_paused(str(job["id"]), reason="host_exit")
            return
        state = result.get("state", "failed")
        job["state"] = {
            "noop": "noop",
            "needs_apply": "needs_apply",
            "applied": "applied",
            "paused": "paused",
            "needs_confirm": "needs_confirm",
        }.get(state, state)
        job["candidate_id"] = result.get("candidate_id")
        job["cost_usd"] = result.get("cost_usd", 0.0)
        job["reason"] = result.get("reason", "")
        job["updated_at"] = _now()
        if job["state"] == "paused":
            job["checkpoint"] = {**(job.get("checkpoint") or {}), "stage": "paused"}
        self.store.write_record("jobs", str(job["id"]), job)
        session["status"] = {
            "noop": "noop",
            "needs_apply": "analyzed",
            "applied": "applied",
            "paused": "paused",
            "needs_confirm": "needs_confirm",
        }.get(job["state"], str(job["state"]))
        self.store.write_record("sessions", str(session["id"]), session)
        self.store.append_log(
            "job_finished",
            job_id=job.get("id"),
            state=job["state"],
            candidate_id=job.get("candidate_id"),
            reason=job.get("reason"),
        )

    def _fail(self, job: dict, exc: ConsumerError, *, session: dict | None = None) -> None:
        self._release_reservation(job)
        job["state"] = "failed"
        job["error"] = exc.to_dict()
        job["updated_at"] = _now()
        self.store.write_record("jobs", str(job["id"]), job)
        if session is not None:
            session["status"] = "failed"
            session["error"] = {"code": exc.code, "message": exc.message}
            self.store.write_record("sessions", str(session["id"]), session)
        self.store.append_log("job_failed", job_id=job.get("id"), code=exc.code, message=exc.message)

    def _release_reservation(self, job: dict) -> None:
        reservation = (job.get("checkpoint") or {}).get("reservation")
        if reservation:
            try:
                self.budget.release(str(reservation), reason="job_stopped")
            except Exception:
                pass

    def _mark_paused(self, job_id: str, *, reason: str) -> None:
        job = self.store.read_record("jobs", job_id)
        if not isinstance(job, dict):
            return
        if job.get("state") in ("running", "queued"):
            self._release_reservation(job)
            job["state"] = "paused"
            job["reason"] = reason
            job["updated_at"] = _now()
            job["checkpoint"] = {**(job.get("checkpoint") or {}), "stage": "paused"}
            self.store.write_record("jobs", job_id, job)
            self.store.append_log("job_paused", job_id=job_id, reason=reason)

    def _mark_canceled(self, job: dict) -> None:
        self._release_reservation(job)
        job["state"] = "canceled"
        job["updated_at"] = _now()
        self.store.write_record("jobs", str(job["id"]), job)
        session = self.store.read_record("sessions", str(job.get("session_id")))
        if isinstance(session, dict):
            session["status"] = "canceled"
            self.store.write_record("sessions", str(job.get("session_id")), session)
        self.store.append_log("job_canceled", job_id=job.get("id"))

    def retry(self, job_id: str, *, force: bool = True) -> dict:
        job = self.store.read_record("jobs", job_id)
        if not isinstance(job, dict):
            raise ConsumerError("NOT_FOUND", "任务不存在。", "刷新历史页。")
        if job.get("state") in ("queued", "running", "paused"):
            return job
        job["state"] = "queued"
        job["force"] = bool(force or job.get("force"))
        job["error"] = None
        job["attempts"] = 0
        job["updated_at"] = _now()
        self.store.write_record("jobs", job_id, job)
        self._queue.put(job_id)
        self.store.append_log("job_retry", job_id=job_id)
        return job

    def cancel(self, job_id: str) -> dict:
        job = self.store.read_record("jobs", job_id)
        if not isinstance(job, dict):
            raise ConsumerError("NOT_FOUND", "任务不存在。", "刷新历史页。")
        if job.get("state") in ("queued", "paused"):
            self._mark_canceled(job)
            return job
        if job.get("state") == "running":
            self._cancel.add(job_id)
            self.store.append_log("job_cancel_requested", job_id=job_id)
            return {**job, "cancel_requested": True}
        raise ConsumerError("INVALID_STATE", "这个任务已经结束，不能取消。", "刷新历史页。")
