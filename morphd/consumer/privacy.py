"""Send previews, redaction, retention, export and deletion."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import sanitize
from .. import store as engine_store
from .errors import ConsumerError
from .hosts import HostManager
from .settings import ProjectManager, SettingsManager
from .store import ConsumerStore
from .util import excluded, is_secret_path

EXPORT_FILES = ("settings", "providers", "projects", "sessions", "jobs", "candidates")


def _session_workspace(store: ConsumerStore, session_id: str) -> Path:
    return store.home / "sessions" / session_id / "workspace"


def preview_payload(store: ConsumerStore, session: dict, settings: dict) -> dict:
    """Build a redacted view of what is actually sent for this session.

    The proposer sees the current harness files, the user turns and the redacted
    trajectory summary. Workspace source files are *not* uploaded; the preview lists only
    their paths as excluded context so the promise matches the real request.
    """
    workspace = Path(str(session.get("workspace") or ""))
    if not workspace.is_dir():
        source = Path(str(session.get("source_workspace") or ""))
        workspace = source if source.is_dir() else _session_workspace(store, str(session.get("id", "")))
    globs = list(settings.get("privacy", {}).get("excluded_globs", []) or [])
    project = ProjectManager(store).for_root(Path(session.get("project") or workspace), create=False)
    globs.extend(project.get("excluded_globs", []) or [])
    harness: dict[str, str] = {}
    host = str(session.get("host") or "")
    if host in ("dsh", "claude-code", "codex"):
        try:
            harness = HostManager(store).read_harness_tree(
                host, project_root=str(session.get("project") or workspace), include_base=True
            )
        except Exception:
            harness = {}
    files: dict[str, str] = {}
    file_redactions = []
    skipped: list[str] = []
    for rel, text in sorted(harness.items()):
        if excluded(rel, globs) or is_secret_path(rel):
            skipped.append(rel)
            continue
        cleaned, labels = sanitize.redact_text(text)
        if labels:
            # Secret-bearing harness files are excluded from the request instead of being
            # sent with placeholders that a model could copy back into the real file.
            skipped.append(rel)
            file_redactions.append({"path": rel, "labels": labels})
            continue
        files[rel] = cleaned
    if workspace.is_dir():
        for path in engine_store.iter_files(workspace):
            rel = path.relative_to(workspace).as_posix()
            if excluded(rel, globs) or is_secret_path(rel):
                skipped.append(rel)
    skipped = skipped[:200]
    turns = []
    for turn in session.get("user_turns", []) or []:
        text, labels = sanitize.redact_text(str(turn.get("text", "")))
        turns.append({"text": text, "corrective": bool(turn.get("corrective")), "redactions": labels})
    traj, labels = sanitize.redact_text(str(session.get("trajectory_summary", "")))
    payload = {
        "session_id": session.get("id"),
        "host": session.get("host"),
        "project": Path(str(session.get("project") or "project")).name,
        "user_turns": turns,
        "trajectory_summary": traj,
        "files": files,
        "excluded": skipped,
        "truncated": False,
        "redactions": {
            "text": labels,
            "files": file_redactions,
            "excluded_globs": globs,
        },
        "estimated_tokens": max(
            1,
            sum(len(v) for v in files.values()) // 4
            + sum(len(t["text"]) for t in turns) // 4
            + len(traj) // 4,
        ),
        "note": "Workspace source contents are not sent. Secret-bearing harness files are skipped; turns and trajectory are redacted.",
    }
    return payload


def _export_provider(provider: dict) -> dict:
    """Strip secret-bearing fields even from legacy or hand-edited provider records."""
    secret_parts = ("api_key", "apikey", "secret", "password", "token", "authorization", "credential")
    clean = {}
    for key, value in provider.items():
        lowered = str(key).lower()
        if lowered in ("key_ref", "key_hint") or not any(part in lowered for part in secret_parts):
            clean[key] = value
    clean["key_ref"] = provider.get("key_ref", "")
    return clean


def export_bundle(store: ConsumerStore) -> dict:
    settings = SettingsManager(store).load()
    providers = store.read_json(store.providers_path, default={"version": 1, "providers": []})
    safe_providers = {
        "version": 1,
        "providers": [_export_provider(p) for p in providers.get("providers", [])],
    }
    snapshots = []
    for manifest in store.list_records("snapshots"):
        snapshots.append({k: v for k, v in manifest.items() if k != "entries"})
    return {
        "version": 1,
        "exported_at": engine_store.utc_now(),
        "settings": settings,
        "providers": safe_providers,
        "projects": store.list_records("projects"),
        "sessions": store.list_records("sessions"),
        "jobs": store.list_records("jobs"),
        "candidates": [
            {
                key: value
                for key, value in candidate.items()
                if key not in ("base_tree", "candidate_tree", "applied_files")
            }
            for candidate in store.list_records("candidates")
        ],
        "snapshots_manifest": snapshots,
        "note": "API keys are not exported. Snapshot file contents are not included.",
    }


def write_export(store: ConsumerStore) -> Path:
    out = store.home / "exports" / f"morphd-export-{engine_store.utc_stamp()}.json"
    engine_store.atomic_write_text(out, json.dumps(export_bundle(store), indent=2, ensure_ascii=False))
    return out


def delete_scope(
    store: ConsumerStore,
    scope: str,
    *,
    session_id: str | None = None,
    project_id: str | None = None,
) -> dict:
    if scope not in ("session", "project", "all"):
        raise ConsumerError("INVALID_REQUEST", "未知的删除范围", "选择会话、项目或全部数据。")
    removed = {"sessions": 0, "jobs": 0, "candidates": 0, "snapshots": 0, "files": 0}
    sessions = store.list_records("sessions")
    jobs = store.list_records("jobs")
    candidates = store.list_records("candidates")

    def match(record: dict) -> bool:
        if scope == "all":
            return True
        if scope == "session":
            return record.get("session_id") == session_id or record.get("id") == session_id
        return record.get("project_id") == project_id

    session_ids: set[str] = set()
    candidate_ids: set[str] = set()
    for record in sessions:
        if match(record):
            session_ids.add(str(record.get("id")))
            store.delete_record("sessions", str(record.get("id")))
            shutil.rmtree(store.home / "sessions" / str(record.get("id")), ignore_errors=True)
            removed["sessions"] += 1
    for record in candidates:
        if match(record) or record.get("session_id") in session_ids:
            candidate_ids.add(str(record.get("id")))
    for record in jobs:
        if match(record) or record.get("session_id") in session_ids:
            jid = str(record.get("id"))
            store.delete_record("jobs", jid)
            shutil.rmtree(store.home / "jobs" / jid, ignore_errors=True)
            removed["jobs"] += 1
    for record in candidates:
        if match(record) or record.get("job_id") in {
            j.get("id") for j in jobs if match(j) or j.get("session_id") in session_ids
        }:
            cid = str(record.get("id"))
            candidate_ids.add(cid)
            store.delete_record("candidates", cid)
            try:
                (store.home / "diffs" / f"{cid}.diff").unlink()
            except FileNotFoundError:
                pass
            shutil.rmtree(store.home / "candidates" / cid, ignore_errors=True)
            removed["candidates"] += 1
    for manifest in store.list_records("snapshots"):
        if (
            match(manifest)
            or manifest.get("project_id") == project_id
            or manifest.get("candidate_id") in candidate_ids
        ):
            sid = str(manifest.get("id"))
            store.delete_record("snapshots", sid)
            shutil.rmtree(store.home / "snapshots" / sid, ignore_errors=True)
            removed["snapshots"] += 1
    if scope == "project" and project_id:
        ProjectManager(store).delete(project_id)
    if scope == "all":
        for project in store.list_records("projects"):
            store.delete_record("projects", str(project.get("id")))
        for sub in ("spool", "exports", "library", "backups", "runtime/cache", "proposals"):
            shutil.rmtree(store.home / sub, ignore_errors=True)
            if sub in ("spool", "exports"):
                (store.home / sub).mkdir(parents=True, exist_ok=True)
        for name in ("manual-projects.json", "state.json"):
            try:
                (store.home / name).unlink()
            except FileNotFoundError:
                pass
        for path in (store.home / "logs").glob("events*.jsonl"):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        state = store.runtime_state_path
        try:
            state.unlink()
        except FileNotFoundError:
            pass
    store.append_log("privacy_delete", scope=scope, removed=removed)
    return removed


def retention_sweep(store: ConsumerStore, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    settings = SettingsManager(store).load()
    default_days = int(settings.get("privacy", {}).get("retention_days", 30))
    projects = {p["id"]: p for p in ProjectManager(store).list() if p.get("id")}
    removed = {"sessions": 0, "jobs": 0, "candidates": 0}
    session_project = {}
    for session in store.list_records("sessions"):
        pid = session.get("project_id")
        session_project[session.get("id")] = pid
        days = int((projects.get(pid, {}) or {}).get("retention_days") or default_days)
        if days <= 0:
            continue
        try:
            ended = datetime.fromisoformat(str(session.get("ended_at")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ended + timedelta(days=days) >= now:
            continue
        store.delete_record("sessions", str(session.get("id")))
        shutil.rmtree(store.home / "sessions" / str(session.get("id")), ignore_errors=True)
        removed["sessions"] += 1
    deleted_job_ids: set[str] = set()
    for job in store.list_records("jobs"):
        sid = job.get("session_id")
        if sid in session_project and not (store.home / "sessions" / f"{sid}.json").exists():
            deleted_job_ids.add(str(job.get("id")))
            store.delete_record("jobs", str(job.get("id")))
            shutil.rmtree(store.home / "jobs" / str(job.get("id")), ignore_errors=True)
            removed["jobs"] += 1
    for candidate in store.list_records("candidates"):
        if candidate.get("session_id") in session_project and not (
            store.home / "sessions" / f"{candidate.get('session_id')}.json"
        ).exists():
            cid = str(candidate.get("id"))
            store.delete_record("candidates", cid)
            try:
                (store.home / "diffs" / f"{cid}.diff").unlink()
            except FileNotFoundError:
                pass
            shutil.rmtree(store.home / "candidates" / cid, ignore_errors=True)
            removed["candidates"] += 1
        elif candidate.get("job_id") in deleted_job_ids:
            cid = str(candidate.get("id"))
            store.delete_record("candidates", cid)
            try:
                (store.home / "diffs" / f"{cid}.diff").unlink()
            except FileNotFoundError:
                pass
            shutil.rmtree(store.home / "candidates" / cid, ignore_errors=True)
            removed["candidates"] += 1
    return removed


def diagnostics(store: ConsumerStore, *, app_version: str, host_checks: list[dict] | None = None) -> dict:
    from .providers import ProvidersManager

    settings = SettingsManager(store).load()
    providers = ProvidersManager(store).public_providers()
    local_checks = [
        {"id": "permissions", "name": "存储权限", "ok": store.permissions_ok(), "detail": "当前用户可读写"},
        {"id": "telemetry", "name": "遥测", "ok": not settings.get("privacy", {}).get("telemetry"), "detail": "默认关闭"},
    ]
    return {
        "version": 1,
        "app_version": app_version,
        "home": str(store.home),
        "permissions_ok": store.permissions_ok(),
        "disk": store.disk_usage(),
        "hosts": host_checks or [],
        "checks": local_checks + list(host_checks or []),
        "providers": [
            {
                "id": p.get("id"),
                "label": p.get("label"),
                "protocol": p.get("protocol"),
                "model": p.get("model"),
                "key_configured": bool(p.get("key_ref")),
                "price_confirmed": bool((p.get("price") or {}).get("confirmed")),
                "local": bool(p.get("local")),
            }
            for p in providers
        ],
        "budget": settings.get("budget", {}),
        "logs_tail": store.log_tail(50),
    }
