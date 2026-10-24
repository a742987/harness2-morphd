"""Project-only read-only scopes for manual analysis.

A manual project selects a harness kind and a project folder *without* adapting the host.
Registering a scope only records it in ``<home>/manual-projects.json``. Importing reads the
latest local session file from the host configuration and stores it as a normal session
record; it never enqueues a job, calls a model, or writes to the host configuration or the
selected project.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable

from .. import store as engine_store
from .. import tasksource
from .errors import ConsumerError
from .hosts import HOST_LABELS, HostManager
from .session_import import ingest, save_session
from .store import ConsumerStore

MANUAL_PROJECTS_VERSION = 1
MANUAL_HOSTS = ("dsh", "claude-code", "codex")
_CONFIG_ENV = {
    "dsh": "DSH_HOME",
    "claude-code": "CLAUDE_CONFIG_DIR",
    "codex": "CODEX_HOME",
}
_CONFIG_DIR_NAME = {
    "dsh": ".dsh",
    "claude-code": ".claude",
    "codex": ".codex",
}


def manual_scope_id(host: str, project_root: Path) -> str:
    """Deterministic id for a host + resolved project root pair."""
    root = os.path.normcase(str(Path(project_root).resolve()))
    digest = hashlib.sha256(f"{host}\x00{root}".encode("utf-8", errors="replace")).hexdigest()
    return f"manual_{digest[:16]}"


def _chmod_private(path: Path, mode: int = 0o600) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _resolve_directory(value, label: str) -> Path:
    text = str(value or "").strip()
    if not text:
        raise ConsumerError(
            "INVALID_REQUEST",
            f"请选择{label}。",
            f"选择一个存在的{label}后重试。",
        )
    try:
        path = Path(text).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ConsumerError(
            "INVALID_REQUEST",
            f"{label}路径无效。",
            f"重新选择{label}。",
            details={"error": str(exc)},
        ) from exc
    if not path.exists():
        raise ConsumerError(
            "INVALID_REQUEST",
            f"{label}不存在。",
            f"选择一个存在的{label}后重试。",
        )
    if not path.is_dir():
        raise ConsumerError(
            "INVALID_REQUEST",
            f"{label}不是文件夹。",
            f"选择一个{label}，而不是文件。",
        )
    return path


def _resolve_optional_directory(value, label: str) -> Path | None:
    # An omitted or blank optional folder means "use the host's default config dirs".
    if value is None or not str(value).strip():
        return None
    return _resolve_directory(value, label)


def _jsonl_files(root: Path) -> list[Path]:
    try:
        return [path for path in root.rglob("*.jsonl") if path.is_file()]
    except OSError:
        return []


def _find_cwd(value, depth: int = 0) -> str:
    if depth > 4:
        return ""
    if isinstance(value, dict):
        cwd = value.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            return cwd.strip()
        for key in ("payload", "session", "meta", "session_meta", "turn_context"):
            nested = value.get(key)
            found = _find_cwd(nested, depth + 1)
            if found:
                return found
        for nested in value.values():
            if isinstance(nested, (dict, list)):
                found = _find_cwd(nested, depth + 1)
                if found:
                    return found
    elif isinstance(value, list):
        for nested in value[:20]:
            found = _find_cwd(nested, depth + 1)
            if found:
                return found
    return ""


def _session_cwd(host: str, path: Path) -> str:
    if host == "dsh":
        try:
            return str(tasksource.read_session(path).cwd or "")
        except Exception:
            return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index > 200:
                    break
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                cwd = _find_cwd(payload)
                if cwd:
                    return cwd
    except OSError:
        return ""
    return ""


def _matches_project(host: str, path: Path, project_root: Path) -> bool:
    cwd = _session_cwd(host, path)
    if not cwd:
        return False
    try:
        session_root = Path(cwd).expanduser().resolve()
        project = Path(project_root).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    try:
        return session_root == project or session_root.is_relative_to(project) or project.is_relative_to(session_root)
    except ValueError:
        return False


def latest_session_file(
    host: str, config_dirs: Iterable[Path], *, project_root: Path | None = None
) -> Path | None:
    """Return the newest local session file for ``host`` under the given config dirs.

    Missing or unreadable directories are ignored. Only session files are enumerated; the
    project folder is never scanned. When ``project_root`` is supplied, files whose recorded
    cwd does not belong to that project are skipped rather than attributed to the wrong
    project.
    """
    candidates: list[Path] = []
    for directory in config_dirs:
        try:
            if not Path(directory).is_dir():
                continue
        except OSError:
            continue
        if host == "dsh":
            sessions_root = Path(directory) / "sessions"
            if sessions_root.is_dir():
                try:
                    candidates.extend(tasksource.discover_sessions(sessions_root))
                except OSError:
                    continue
        elif host == "claude-code":
            for name in ("projects", "sessions"):
                base = Path(directory) / name
                if base.is_dir():
                    candidates.extend(_jsonl_files(base))
        elif host == "codex":
            base = Path(directory) / "sessions"
            if base.is_dir():
                candidates.extend(_jsonl_files(base))

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    existing = []
    for path in candidates:
        try:
            if path.is_file():
                existing.append(path)
        except OSError:
            continue
    if not existing:
        return None
    ordered = sorted(existing, key=_mtime, reverse=True)
    if project_root is None:
        return ordered[0]
    for path in ordered:
        if _matches_project(host, path, project_root):
            return path
    return None


def config_dirs_for(scope: dict) -> list[Path]:
    """Resolve the config dirs to search for a scope.

    An explicit ``config_dir`` wins. Otherwise the host's documented environment variable
    is honoured, then the default per-user config directory is used.
    """
    explicit = str(scope.get("config_dir") or "").strip()
    if explicit:
        return [Path(explicit).expanduser()]
    host = str(scope.get("host") or "")
    env_name = _CONFIG_ENV.get(host)
    env_value = os.environ.get(env_name) if env_name else None
    if env_value and str(env_value).strip():
        return [Path(env_value).expanduser()]
    name = _CONFIG_DIR_NAME.get(host)
    return [Path.home() / name] if name else []


class ManualProjectManager:
    """Persistence and import for project-only read-only scopes."""

    def __init__(self, store: ConsumerStore):
        self.store = store
        self.path = store.home / "manual-projects.json"

    # --- persistence ---------------------------------------------------------
    def _load(self) -> dict:
        payload = self.store.read_json(self.path, default=None)
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault("version", MANUAL_PROJECTS_VERSION)
        projects = payload.get("projects")
        payload["projects"] = (
            [dict(item) for item in projects if isinstance(item, dict)]
            if isinstance(projects, list)
            else []
        )
        return payload

    def _save(self, payload: dict) -> None:
        document = dict(payload)
        document["version"] = MANUAL_PROJECTS_VERSION
        document["projects"] = list(document.get("projects") or [])
        engine_store.atomic_write_json(self.path, document)
        _chmod_private(self.path)

    def list(self) -> list[dict]:
        projects = [dict(record) for record in self._load()["projects"]]
        return sorted(projects, key=lambda record: str(record.get("created_at") or ""), reverse=True)

    def get(self, scope_id: str) -> dict:
        for record in self._load()["projects"]:
            if record.get("id") == scope_id:
                return dict(record)
        raise ConsumerError(
            "NOT_FOUND",
            "这个只读项目不存在。",
            "刷新页面后重新选择项目文件夹。",
        )

    # --- lifecycle -----------------------------------------------------------
    def register(self, payload: dict) -> dict:
        host = str(payload.get("host") or "").strip()
        if host not in MANUAL_HOSTS:
            raise ConsumerError(
                "HOST_UNSUPPORTED",
                "只读项目模式不支持这个宿主。",
                "使用 dsh、Claude Code 或 Codex。",
            )
        project_root = _resolve_directory(payload.get("project_root"), "项目文件夹")
        config_dir = _resolve_optional_directory(payload.get("config_dir"), "配置文件夹")
        if config_dir is not None and not HostManager._looks_like_config(host, config_dir):
            raise ConsumerError(
                "INVALID_REQUEST",
                "这个文件夹不像目标 Agent 工具的配置目录。",
                "选择 ~/.dsh、~/.claude 或 ~/.codex，或留空使用默认目录。",
            )
        label = str(payload.get("label") or "").strip() or project_root.name or str(project_root)
        scope_id = manual_scope_id(host, project_root)
        now = engine_store.utc_now()

        document = self._load()
        records = document["projects"]
        record: dict = {}
        for index, item in enumerate(records):
            if item.get("id") == scope_id:
                record = dict(item)
                records[index] = record
                break
        else:
            records.append(record)

        record.update(
            {
                "id": scope_id,
                "host": host,
                "project_root": str(project_root),
                "config_dir": str(config_dir) if config_dir is not None else None,
                "label": label,
                "read_only": True,
                "auto_trigger": False,
                "created_at": str(record.get("created_at") or now),
                "updated_at": now,
            }
        )
        self._save(document)
        return dict(record)

    def delete(self, scope_id: str) -> dict:
        document = self._load()
        records = document["projects"]
        remaining = [record for record in records if record.get("id") != scope_id]
        if len(remaining) == len(records):
            raise ConsumerError(
                "NOT_FOUND",
                "这个只读项目不存在。",
                "刷新页面后重试。",
            )
        document["projects"] = remaining
        self._save(document)
        return {"ok": True}

    def import_latest(self, scope_id: str) -> dict:
        scope = self.get(scope_id)
        host = str(scope.get("host") or "")
        if host not in MANUAL_HOSTS:
            raise ConsumerError(
                "HOST_UNSUPPORTED",
                "只读项目模式不支持这个宿主。",
                "删除这个只读项目后重新选择。",
            )
        project_root = _resolve_directory(scope.get("project_root"), "项目文件夹")
        session_path = latest_session_file(
            host, config_dirs_for(scope), project_root=project_root
        )
        if session_path is None:
            label = HOST_LABELS.get(host, host)
            raise ConsumerError(
                "NOT_FOUND",
                f"没有找到属于这个项目的 {label} 本地会话。",
                "先在这个项目中完成一次会话，或检查只读项目选择的配置文件夹。",
            )
        context = ingest(
            host=host,
            events_path=session_path,
            workspace=project_root,
            project_root=project_root,
        )
        return save_session(self.store, context, source_workspace=project_root)
