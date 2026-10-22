"""Application service shared by the local API, CLI and desktop shell."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .. import store as engine_store
from . import API_VERSION, APP_VERSION, catalog
from .attention import AttentionManager
from .budget import ConsumerBudget
from .candidates import CandidateManager
from .errors import ConsumerError
from .hosts import HOST_LABELS, HostManager
from .jobs import JobRunner
from .keychain import KeyVault
from .privacy import delete_scope, export_bundle, retention_sweep, write_export
from .privacy import diagnostics as build_diagnostics
from .providers import ProvidersManager, estimate_analysis_usd
from .session_import import context_from_payload, ingest
from .settings import (
    DEFAULT_SETTINGS,
    ProjectManager,
    SettingsManager,
    effective_settings,
)
from .store import ConsumerStore


class ConsumerService:
    def __init__(self, home: Path | None = None):
        home = home or Path(os.environ.get("MORPHD_HOME", Path.home() / ".morphd"))
        self.store = ConsumerStore(home)
        self.settings_manager = SettingsManager(self.store)
        if not self.store.config_path.exists():
            self.settings_manager.save(DEFAULT_SETTINGS)
        self.projects = ProjectManager(self.store)
        self.hosts = HostManager(self.store)
        self.attention = AttentionManager(self.store)
        self.budget = ConsumerBudget(self.store)
        self.providers = ProvidersManager(self.store)
        self.candidates = CandidateManager(self.store)
        self.jobs = JobRunner(self.store)
        self.keyvault = KeyVault(self.store.home)
        self.started_at = engine_store.utc_now()
        self.connected_hosts: dict[str, dict] = {}

    # --- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        self.budget.release_stale()
        self._drain_spool()
        self.jobs.start()
        self.store.append_log("service_started", version=APP_VERSION)

    def stop(self) -> None:
        self.jobs.stop()
        self.store.append_log("service_stopped")

    # --- status ---------------------------------------------------------------
    def status(self) -> dict:
        settings = self.settings_manager.load()
        state = self.attention.state()
        budget = self.budget.snapshot(
            monthly_limit=float(settings["budget"]["monthly_usd"]),
            session_limit=float(settings["budget"]["per_session_usd"]),
            job_limit=float(settings["budget"]["per_job_usd"]),
            alert_ratio=float(settings["budget"]["alert_ratio"]),
        )
        installed = self.hosts.install_records()
        return {
            "version": API_VERSION,
            "app_version": APP_VERSION,
            "hosts": [
                {
                    "host": record.get("host"),
                    "label": HOST_LABELS.get(record.get("host"), record.get("host")),
                    "version": record.get("version"),
                    "integration": record.get("integration"),
                    "status": record.get("status"),
                    "connected": bool(self.connected_hosts.get(record.get("host"))),
                    "wrapper": record.get("wrapper"),
                    "project_root": record.get("project_root"),
                }
                for record in installed
            ],
            "connected_hosts": sorted(self.connected_hosts),
            "learning": {
                "enabled": bool(settings.get("enabled")),
                "paused": self.attention.is_paused(),
                "paused_until": state.get("paused_until"),
                "paused_forever": bool(state.get("paused_forever")),
                "vacation": bool(state.get("vacation")),
                "incognito_next": bool(state.get("incognito_next")),
                "incognito_sessions": state.get("incognito_sessions", []),
                "active_sessions": state.get("active_sessions", []),
                "focus": bool(state.get("active_sessions")),
                "trust_level": settings.get("trust_level"),
                "change_appetite": settings.get("change_appetite"),
            },
            "budget": budget.to_dict(),
            "job": {
                "running": self.jobs.current_job,
                "queued": len([j for j in self.store.list_records("jobs") if j.get("state") in ("queued", "running")]),
            },
            "tray": {
                "available": _tray_supported(),
                "status": self.tray_status(settings),
            },
            "vault": self.keyvault.backend_status(),
            "started_at": self.started_at,
        }

    def tray_status(self, settings: dict | None = None) -> str:
        settings = settings or self.settings_manager.load()
        if self.attention.is_paused():
            return "paused"
        if self.attention.is_incognito(None):
            return "incognito"
        snapshot = self.budget.snapshot(
            monthly_limit=float(settings["budget"]["monthly_usd"]),
            session_limit=float(settings["budget"]["per_session_usd"]),
            job_limit=float(settings["budget"]["per_job_usd"]),
        )
        if snapshot.blocked:
            return "budget_exhausted"
        if not any(r.get("status") in ("adapted", "beta") for r in self.hosts.install_records()):
            return "not_connected"
        if self.jobs.current_job:
            return "learning"
        if not settings.get("models", {}).get("proposer"):
            return "needs_setup"
        return "idle"

    # --- settings -------------------------------------------------------------
    def get_settings(self) -> dict:
        settings = self.settings_manager.load()
        state = self.attention.state()
        payload = dict(settings)
        payload["_runtime"] = {
            "paused": self.attention.is_paused(),
            "paused_until": state.get("paused_until"),
            "paused_forever": bool(state.get("paused_forever")),
            "vacation": bool(state.get("vacation")),
            "incognito_next": bool(state.get("incognito_next")),
        }
        return payload

    def put_settings(self, payload: dict) -> dict:
        payload = {key: value for key, value in payload.items() if not key.startswith("_")}
        current = self.settings_manager.load()
        risky = payload.get("trust_level") == "autopilot" or (
            payload.get("auto_apply") and current.get("trust_level") != "assistant"
        )
        if risky and not payload.get("autopilot_ack"):
            raise ConsumerError(
                "INVALID_REQUEST",
                "这个改动会让 morphd 自动修改 harness，需要再次确认。",
                "勾选确认并重新保存。",
            )
        updated = self.settings_manager.save(payload)
        if not updated.get("enabled", True):
            self.store.append_log("learning_disabled")
        return updated

    def reset_settings(self) -> dict:
        return self.settings_manager.reset()

    # --- projects -------------------------------------------------------------
    def projects_list(self) -> list[dict]:
        return self.projects.list()

    def project_get(self, project_id: str) -> dict:
        record = self.store.read_record("projects", project_id)
        if not isinstance(record, dict):
            raise ConsumerError("NOT_FOUND", "项目不存在。", "刷新页面。")
        return record

    def project_put(self, project_id: str, payload: dict) -> dict:
        return self.projects.save(project_id, payload)

    # --- providers ------------------------------------------------------------
    def providers_list(self) -> dict:
        return {
            "providers": self.providers.public_providers(),
            "catalog": catalog.provider_help(),
            "presets": catalog.MODEL_PRESETS,
            "vault": self.keyvault.backend_status(),
        }

    def provider_add(self, payload: dict) -> dict:
        return self.providers.add(payload, payload.get("api_key"))

    def provider_update(self, provider_id: str, payload: dict) -> dict:
        return self.providers.update(provider_id, payload, payload.get("api_key"))

    def provider_delete(self, provider_id: str) -> dict:
        self.providers.delete(provider_id)
        return {"ok": True, "id": provider_id}

    def provider_test(self, provider_id: str | None, payload: dict | None = None) -> dict:
        return self.providers.test(provider_id, payload, (payload or {}).get("api_key"))

    def vault_unlock(self, passphrase: str) -> dict:
        self.keyvault.unlock_vault(passphrase)
        return self.keyvault.backend_status()

    def vault_lock(self) -> dict:
        self.keyvault.lock_vault()
        return self.keyvault.backend_status()

    # --- hosts ----------------------------------------------------------------
    def hosts_discover(self, *, force: bool = False) -> list[dict]:
        settings = self.settings_manager.load()
        if not force and not settings.get("privacy", {}).get("auto_discover_hosts", True):
            return self.hosts.discover(kinds=[])
        return self.hosts.discover(force=force)

    def hosts_adapt(self, payload: dict) -> dict:
        if payload.get("manual"):
            candidate = self.hosts.validate_manual(
                str(payload.get("kind")),
                install_path=str(payload.get("install_path") or ""),
                config_path=payload.get("config_path"),
                project_root=payload.get("project_root"),
            )
        else:
            candidate = payload.get("candidate") or payload
        project = payload.get("project_root") or candidate.get("project_root")
        if project and not Path(str(project)).expanduser().is_dir():
            raise ConsumerError(
                "INVALID_REQUEST",
                "项目文件夹不存在。",
                "选择一个存在的项目目录，或留空使用宿主配置目录。",
            )
        record = self.hosts.adapt(
            candidate,
            project_root=payload.get("project_root"),
            register_plugin=bool(payload.get("register_plugin", False)),
        )
        return record

    def host_uninstall(self, kind: str) -> dict:
        result = self.hosts.uninstall(kind)
        self.connected_hosts.pop(kind, None)
        return result

    def host_connect(self, kind: str) -> None:
        if kind in HOST_LABELS:
            self.connected_hosts[kind] = {"since": engine_store.utc_now()}

    def host_disconnect(self, kind: str) -> None:
        self.connected_hosts.pop(kind, None)
        self.attention.clear_host_sessions(kind)

    def pick_folder(self, kind: str = "install") -> dict:
        if kind not in ("install", "config", "project"):
            raise ConsumerError("INVALID_REQUEST", "未知的文件夹类型。", "选择安装、配置或项目目录。")
        path = _pick_folder_native()
        if not path:
            raise ConsumerError(
                "INVALID_REQUEST",
                "当前桌面环境无法打开系统文件夹选择器。",
                "直接输入路径；Electron 桌面版提供原生选择器。",
            )
        return {"path": path}

    # --- sessions / jobs ------------------------------------------------------
    def sessions_list(self, *, limit: int = 100, project_id: str | None = None) -> list[dict]:
        records = self.store.list_records("sessions", limit=limit)
        if project_id:
            records = [r for r in records if r.get("project_id") == project_id]
        return [self.public_session(record) for record in records]

    def session_get(self, session_id: str) -> dict:
        record = self.store.read_record("sessions", session_id)
        if not isinstance(record, dict):
            raise ConsumerError("NOT_FOUND", "会话不存在。", "刷新历史页。")
        return record

    @staticmethod
    def public_session(record: dict) -> dict:
        return {
            "version": 1,
            "id": record.get("id"),
            "host": record.get("host"),
            "project_id": record.get("project_id"),
            "project": record.get("project"),
            "started_at": record.get("started_at"),
            "ended_at": record.get("ended_at"),
            "end_reason": record.get("end_reason"),
            "signals": record.get("signals"),
            "status": record.get("status"),
            "job_id": record.get("job_id"),
            "user_turns_count": len(record.get("user_turns") or []),
            "summary": (record.get("trajectory_summary") or "")[-1200:],
            "redaction": record.get("redaction"),
            "error": record.get("error"),
            "cost_usd": record.get("cost_usd"),
        }

    def session_analyze(self, session_id: str, *, confirm: bool = False) -> dict:
        if not confirm:
            preview = self.privacy_preview(session_id)
            estimate = None
            try:
                session = self.session_get(session_id)
                project = self.store.read_record("projects", str(session.get("project_id"))) or {}
                effective = effective_settings(self.settings_manager.load(), project)
                clients = self.providers.clients_for_role(effective, "proposer")
                file_chars = sum(len(str(text)) for text in (preview.get("files") or {}).values())
                turn_chars = sum(
                    len(str(turn.get("text", ""))) for turn in preview.get("user_turns", []) or []
                )
                context_chars = file_chars + turn_chars + len(str(preview.get("trajectory_summary", "")))
                estimate = estimate_analysis_usd(clients[0].provider, context_chars=context_chars, calls=9)
            except ConsumerError:
                estimate = None
            return {
                "requires_confirmation": True,
                "preview": preview,
                "estimated_cost_usd": estimate,
            }
        session = self.session_get(session_id)
        if self.attention.is_incognito(session_id):
            raise ConsumerError("FORBIDDEN", "这是隐身会话，不能分析。", "关闭隐身后从新会话开始。")
        job = next(
            (j for j in self.store.list_records("jobs") if j.get("session_id") == session_id),
            None,
        )
        if job and job.get("state") in ("queued", "running", "paused"):
            return {"ok": True, "job_id": job.get("id"), "already": True}
        if job and job.get("state") in ("needs_apply", "applied", "trial", "kept"):
            return {
                "ok": True,
                "job_id": job.get("id"),
                "candidate_id": job.get("candidate_id"),
                "already": True,
            }
        if job:
            retried = self.jobs.retry(str(job["id"]), force=True)
            job_id = retried["id"]
        else:
            if not str(session.get("source_workspace") or "").strip():
                raise ConsumerError("INVALID_REQUEST", "这个会话没有 workspace，无法分析。", "没有可分析的证据。")
            context = context_from_payload(
                {
                    **session,
                    "project_root": session.get("project"),
                    "workspace": session.get("source_workspace") or session.get("workspace"),
                    "events_path": session.get("events_path"),
                    "session_id": session.get("id"),
                    "host": session.get("host"),
                    "user_turns": session.get("user_turns"),
                    "trajectory_summary": session.get("trajectory_summary"),
                    "end_reason": session.get("end_reason"),
                }
            )
            result = self.jobs.handle_session_end(context, manual=True)
            job_id = result.get("job_id")
        session["status"] = "queued"
        self.store.write_record("sessions", session_id, session)
        return {"ok": True, "job_id": job_id, "requires_confirmation": False}

    def jobs_list(self, *, state: str | None = None) -> list[dict]:
        records = self.store.list_records("jobs")
        if state:
            records = [r for r in records if r.get("state") == state]
        return [self.public_job(record) for record in records]

    @staticmethod
    def public_job(record: dict) -> dict:
        return {
            "version": 1,
            "id": record.get("id"),
            "session_id": record.get("session_id"),
            "host": record.get("host"),
            "project_id": record.get("project_id"),
            "state": record.get("state"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "attempts": record.get("attempts"),
            "candidate_id": record.get("candidate_id"),
            "cost_usd": record.get("cost_usd"),
            "reason": record.get("reason"),
            "checkpoint": record.get("checkpoint"),
            "error": record.get("error"),
        }

    def job_cancel(self, job_id: str) -> dict:
        return self.public_job(self.jobs.cancel(job_id))

    # --- candidates -----------------------------------------------------------
    def candidates_list(self, *, state: str | None = None, project_id: str | None = None) -> list[dict]:
        return [
            self.public_candidate(item)
            for item in self.candidates.list(state=state, project_id=project_id)
        ]

    def candidate_get(self, candidate_id: str, *, include_diff: bool = True) -> dict:
        record = self.candidates.get(candidate_id)
        payload = self.public_candidate(record)
        if include_diff:
            try:
                payload["diff"] = Path(record["diff_path"]).read_text(encoding="utf-8", errors="replace")
            except (OSError, KeyError):
                payload["diff"] = ""
        return payload

    @staticmethod
    def public_candidate(record: dict) -> dict:
        return {
            key: value
            for key, value in record.items()
            if key not in ("base_tree", "candidate_tree", "applied_files")
        }

    def candidate_apply(self, candidate_id: str) -> dict:
        return self.public_candidate(self.candidates.apply(candidate_id, confirm=True))

    def candidate_revert(self, candidate_id: str) -> dict:
        return self.public_candidate(self.candidates.revert(candidate_id, confirm=True))

    def candidate_feedback(self, candidate_id: str, payload: dict) -> dict:
        result = self.candidates.feedback(
            candidate_id,
            str(payload.get("reason") or ""),
            note=str(payload.get("note") or ""),
        )
        return {"ok": True, "attention": result.get("attention"), "candidate": self.public_candidate(result["candidate"])}

    def candidate_diff(self, candidate_id: str) -> str:
        record = self.candidates.get(candidate_id)
        try:
            return Path(record["diff_path"]).read_text(encoding="utf-8", errors="replace")
        except (OSError, KeyError):
            return ""

    def restore_original(self, payload: dict) -> dict:
        kind = str(payload.get("host") or "")
        record = self.hosts.record_for(kind)
        if record is None:
            raise ConsumerError("HOST_NOT_FOUND", "还没有为你安装 harness2-morphd。", "先完成宿主适配。")
        project_root = payload.get("project_root") or record.get("project_root") or Path.cwd()
        return self.candidates.restore_original(
            host=kind, project_root=str(project_root), confirm=bool(payload.get("confirm"))
        )

    # --- budget / attention ---------------------------------------------------
    def budget_get(self, *, project_id: str | None = None) -> dict:
        settings = self.settings_manager.load()
        project = self.store.read_record("projects", project_id) if project_id else {}
        effective = effective_settings(settings, project or {})
        snap = self.budget.snapshot(
            monthly_limit=float(effective["budget"]["monthly_usd"]),
            session_limit=float(effective["budget"]["per_session_usd"]),
            job_limit=float(effective["budget"]["per_job_usd"]),
            alert_ratio=float(effective["budget"]["alert_ratio"]),
        )
        payload = snap.to_dict()
        payload["by_kind"] = self.budget.monthly_usage()
        return payload

    def pause(self, until: str | None) -> dict:
        return self.attention.pause(until)

    def resume(self) -> dict:
        return self.attention.resume()

    def vacation(self, active: bool) -> dict:
        return self.attention.set_vacation(active)

    def incognito(self, active: bool, session_id: str | None = None) -> dict:
        return self.attention.set_incognito(active, session_id)

    def session_start(self, payload: dict) -> dict:
        host = str(payload.get("host") or "")
        session_id = str(payload.get("session_id") or "")
        self.host_connect(host)
        if session_id:
            self.attention.session_started(session_id, host)
            if payload.get("incognito"):
                self.attention.set_incognito(True, session_id)
        return {"ok": True, "incognito": self.attention.is_incognito(session_id) if session_id else False}

    def session_end(self, payload: dict) -> dict:
        host = str(payload.get("host") or "")
        if host not in HOST_LABELS:
            raise ConsumerError("HOST_UNSUPPORTED", "未知宿主。", "重新安装宿主桥。")
        events_path = payload.get("events_path")
        workspace = payload.get("workspace") or payload.get("project_root") or Path.cwd()
        if events_path:
            context = ingest(
                host=host,
                events_path=Path(str(events_path)),
                workspace=Path(str(workspace)),
                project_root=Path(str(payload.get("project_root") or workspace)),
                end_reason=payload.get("end_reason"),
                user_turns=payload.get("user_turns"),
            )
            if payload.get("session_id"):
                context.session_id = str(payload["session_id"])
        else:
            context = context_from_payload({**payload, "host": host, "workspace": str(workspace)})
        result = self.jobs.handle_session_end(
            context,
            source_workspace=Path(str(payload.get("source_workspace") or workspace)),
            manual=bool(payload.get("manual")),
        )
        return result

    def ingest_latest_for_host(self, kind: str, *, end_reason: str = "completed") -> dict | None:
        path = _latest_session_file(kind, self.hosts.record_for(kind) or {})
        if path is None:
            return None
        try:
            from datetime import datetime

            started = datetime.fromisoformat(self.started_at.replace("Z", "+00:00")).timestamp()
            if path.stat().st_mtime < started - 10:
                return None
        except (OSError, ValueError):
            pass
        try:
            context = ingest(
                host=kind,
                events_path=path,
                workspace=Path.cwd(),
                project_root=Path.cwd(),
                end_reason=end_reason,
            )
        except Exception as exc:
            self.store.append_log("host_exit_ingest_failed", host=kind, path=str(path), error=str(exc))
            return None
        existing = self.store.read_record("sessions", context.session_id)
        if isinstance(existing, dict) and existing.get("status") not in ("failed", None):
            return {"captured": True, "deduplicated": True, "session_id": context.session_id}
        return self.jobs.handle_session_end(context)

    # --- privacy / diagnostics ------------------------------------------------
    def privacy_preview(self, session_id: str) -> dict:
        session = self.session_get(session_id)
        return self.jobs.analyzer.preview(session)

    def privacy_delete(self, payload: dict) -> dict:
        scope = str(payload.get("scope") or "")
        return delete_scope(
            self.store,
            scope,
            session_id=payload.get("session_id"),
            project_id=payload.get("project_id"),
        )

    def privacy_export(self) -> dict:
        path = write_export(self.store)
        return {"path": str(path), "bundle": export_bundle(self.store)}

    def privacy_retention(self) -> dict:
        return retention_sweep(self.store)

    def reset_learning(self) -> dict:
        """Delete suggestions/jobs and derived trial state; keep settings, keys and sessions."""
        import shutil

        removed = {"candidates": 0, "jobs": 0, "diffs": 0}
        for candidate in self.store.list_records("candidates"):
            cid = str(candidate.get("id"))
            self.store.delete_record("candidates", cid)
            shutil.rmtree(self.store.home / "candidates" / cid, ignore_errors=True)
            removed["candidates"] += 1
        for job in self.store.list_records("jobs"):
            jid = str(job.get("id"))
            self.store.delete_record("jobs", jid)
            shutil.rmtree(self.store.home / "jobs" / jid, ignore_errors=True)
            removed["jobs"] += 1
        for path in (self.store.home / "diffs").glob("*.diff"):
            try:
                path.unlink()
                removed["diffs"] += 1
            except OSError:
                pass
        self.attention.update(weekly={}, feedback={}, alerts={}, last_digest=None)
        self.store.append_log("reset_learning", removed=removed)
        return {"ok": True, **removed, "snapshots_kept": True}

    def diagnostics(self) -> dict:
        checks = []
        for record in self.hosts.install_records():
            checks.append(
                {
                    "id": f"host:{record.get('host')}",
                    "name": f"{record.get('host')} 适配",
                    "ok": record.get("status") in ("adapted", "beta"),
                    "detail": f"{record.get('version')} · {record.get('integration')}",
                }
            )
        for provider in self.providers.public_providers():
            checks.append(
                {
                    "id": f"provider:{provider.get('id')}",
                    "name": f"模型 {provider.get('label')}",
                    "ok": bool(provider.get("has_key") or provider.get("local")),
                    "detail": provider.get("model", ""),
                }
            )
        return build_diagnostics(self.store, app_version=APP_VERSION, host_checks=checks)

    # --- spool ----------------------------------------------------------------
    def spool_event(self, payload: dict) -> Path:
        import uuid

        path = (
            self.store.home
            / "spool"
            / f"event-{engine_store.utc_stamp()}-{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
        )
        engine_store.atomic_write_json(path, payload)
        return path

    def _drain_spool(self) -> None:
        spool = self.store.home / "spool"
        for path in sorted(spool.glob("*.json")):
            payload = engine_store.read_json(path)
            if not isinstance(payload, dict):
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            try:
                if payload.get("type") == "session-end":
                    self.session_end(payload.get("payload") or {})
                elif payload.get("type") == "session-start":
                    # A stale start event from a previous host must not leave focus mode on.
                    try:
                        fresh = time.time() - path.stat().st_mtime < 60
                    except OSError:
                        fresh = False
                    if fresh:
                        self.session_start(payload.get("payload") or {})
                path.unlink()
                self.store.append_log("spool_processed", path=str(path))
            except Exception as exc:
                self.store.append_log("spool_failed", path=str(path), error=str(exc))
                try:
                    path.rename(path.with_suffix(".failed"))
                except OSError:
                    pass


def _tray_supported() -> bool:
    if os.environ.get("MORPHD_TRAY", "1") == "0":
        return False
    if os.name == "nt" or sys.platform == "darwin":
        return True
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return False
    try:
        import PIL  # noqa: F401
        import pystray  # noqa: F401

        return True
    except Exception:
        return False


def _latest_session_file(kind: str, record: dict) -> Path | None:
    config_dir = Path(str(record.get("config_dir") or "")).expanduser()
    candidates: list[Path] = []
    if kind == "dsh":
        from .. import tasksource

        root = Path(os.environ.get("DSH_HOME", config_dir or (Path.home() / ".dsh"))) / "sessions"
        if root.is_dir():
            candidates = tasksource.discover_sessions(root)
    elif kind == "claude-code":
        root = config_dir or (Path.home() / ".claude")
        for base in (root / "projects", root / "sessions"):
            if base.is_dir():
                candidates.extend(base.rglob("*.jsonl"))
    else:
        root = config_dir or (Path.home() / ".codex")
        if (root / "sessions").is_dir():
            candidates.extend((root / "sessions").rglob("*.jsonl"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _pick_folder_native() -> str | None:
    if os.name == "nt":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
            "if($d.ShowDialog() -eq 'OK'){Write-Output $d.SelectedPath}"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            path = (proc.stdout or "").strip()
            return path or None
        except (OSError, subprocess.TimeoutExpired):
            return None
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        for command in (
            ["zenity", "--file-selection", "--directory"],
            ["kdialog", "--getexistingdirectory", str(Path.home())],
        ):
            try:
                proc = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
                path = (proc.stdout or "").strip()
                if proc.returncode == 0 and path:
                    return path
            except (OSError, subprocess.TimeoutExpired):
                continue
    return None
