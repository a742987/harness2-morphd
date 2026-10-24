"""Harness discovery, identity validation, adaptation, wrappers and uninstall."""

from __future__ import annotations

import copy
import csv
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:  # Windows-only product; keep imports safe on non-Windows test hosts.
    import winreg
except ImportError:  # pragma: no cover - exercised only off Windows
    winreg = None  # type: ignore[assignment]

from .. import config, winproc
from .. import store as engine_store
from . import APP_VERSION
from .errors import ConsumerError
from .store import ConsumerStore

HOST_BINARIES = {"dsh": "dsh", "claude-code": "claude", "codex": "codex"}
HOST_LABELS = {"dsh": "dsh", "claude-code": "Claude Code", "codex": "Codex"}
HOST_IDENTITY = {
    "dsh": ("dsh", "deepseek"),
    "claude-code": ("claude",),
    "codex": ("codex",),
}
COMPATIBLE_MINORS = {
    "dsh": (0, 2),
    "claude-code": (2, 1),
    "codex": (0, 144),
}
DEFAULT_CONFIG_DIR = {
    "dsh": [Path.home() / ".dsh"],
    "claude-code": [Path.home() / ".claude"],
    "codex": [Path.home() / ".codex"],
}
_CONFIG_ENV = {
    "dsh": "DSH_HOME",
    "claude-code": "CLAUDE_CONFIG_DIR",
    "codex": "CODEX_HOME",
}
_PROJECT_FILES = {"dsh": "AGENTS.md", "claude-code": "CLAUDE.md", "codex": "AGENTS.md"}
_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
_ACTIVE_RECORD_STATUSES = ("adapted", "beta")
_REGISTRY_UNINSTALL_ROOTS = (
    ("HKEY_CURRENT_USER", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    ("HKEY_LOCAL_MACHINE", r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    (
        "HKEY_LOCAL_MACHINE",
        r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    ),
)
_REGISTRY_APP_PATH_ROOTS = (
    ("HKEY_CURRENT_USER", r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
    ("HKEY_LOCAL_MACHINE", r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
    (
        "HKEY_LOCAL_MACHINE",
        r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths",
    ),
)
_EXECUTABLE_SUFFIXES = (".exe", ".cmd", ".bat")
_REGISTRY_VALUE_NAMES = ("DisplayName", "DisplayVersion", "InstallLocation", "DisplayIcon")
_REGISTRY_MAX_SUBKEYS = 10_000


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(value or "")
    if not match:
        return None
    parts = tuple(int(part) for part in match.group(1).split("."))
    return parts  # type: ignore[return-value]


def _version_text(value: object) -> str:
    return str(value or "").strip().lower().lstrip("v")


def _same_version(left: object, right: object) -> bool:
    return bool(_version_text(left)) and _version_text(left) == _version_text(right)


def _path_key(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    path = Path(raw).expanduser()
    try:
        path = path.resolve()
    except OSError:
        pass
    return os.path.normcase(str(path))


def _same_executable(left: object, right: object) -> bool:
    left_key = _path_key(left)
    return bool(left_key) and left_key == _path_key(right)


def _executable_names(kind: str) -> tuple[str, ...]:
    name = HOST_BINARIES[kind]
    return (f"{name}.exe", f"{name}.cmd", f"{name}.bat", name)


# --- read-only Windows registry discovery -------------------------------------
def _registry_open(registry, root, subkey: str):
    """Open a registry key for reading only. Any failure is non-fatal."""
    try:
        return registry.OpenKey(root, subkey, 0, getattr(registry, "KEY_READ", 0))
    except Exception:
        return None


def _registry_close(registry, key) -> None:
    try:
        registry.CloseKey(key)
    except Exception:
        return


def _registry_value(registry, key, name: str) -> str:
    """Read one explicitly allowed string value; malformed values are ignored."""
    try:
        value, _kind = registry.QueryValueEx(key, name)
    except Exception:
        return ""
    return value.strip() if isinstance(value, str) else ""


def _registry_kind(display_name: str, kinds: list[str]) -> str | None:
    tokens = set(re.findall(r"[a-z0-9]+", (display_name or "").lower()))
    for kind in kinds:
        if kind == "dsh" and ("dsh" in tokens or {"deepseek", "cli"} <= tokens):
            return kind
        if kind == "claude-code" and "claude" in tokens:
            return kind
        if kind == "codex" and "codex" in tokens:
            return kind
    return None


def _clean_registry_path(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text[0] in ("'", '"'):
        end = text.find(text[0], 1)
        if end <= 1:
            return None
        text = text[1:end]
    text = os.path.expandvars(text).strip()
    if not text:
        return None
    try:
        return Path(text)
    except (OSError, ValueError):
        return None


def _clean_icon_path(value: object) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not text.startswith(('"', "'")):
        text = re.sub(r",\s*-?\d+\s*$", "", text)
    return _clean_registry_path(text)


def _install_dir_executables(kind: str, raw_location: object) -> list[Path]:
    """Look in an install location plus its conventional bin folders (no recursion)."""
    location = _clean_registry_path(raw_location)
    if location is None:
        return []
    try:
        if location.is_file():
            return [location]
        if not location.is_dir():
            return []
    except OSError:
        return []
    found: list[Path] = []
    seen: set[str] = set()
    directories = (location, location / "bin", location / "node_modules" / ".bin")
    for directory in directories:
        for name in _executable_names(kind):
            path = directory / name
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            key = _path_key(path)
            if key and key not in seen:
                seen.add(key)
                found.append(path)
    return found


def _registry_executables(kind: str, entry: dict) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()

    def add(path: Path | None) -> None:
        if path is None:
            return
        key = _path_key(path)
        if key and key not in seen:
            seen.add(key)
            found.append(path)

    for path in _install_dir_executables(kind, entry.get("install_location")):
        add(path)
    icon = _clean_icon_path(entry.get("display_icon"))
    if icon is not None and icon.suffix.lower() in _EXECUTABLE_SUFFIXES:
        try:
            if icon.is_file():
                add(icon)
        except OSError:
            pass
    return found


def _uninstall_registry_entries(registry, kinds: list[str]) -> list[dict]:
    entries: list[dict] = []
    for hive_name, subkey in _REGISTRY_UNINSTALL_ROOTS:
        hive = getattr(registry, hive_name, None)
        if hive is None:
            continue
        root = _registry_open(registry, hive, subkey)
        if root is None:
            continue
        try:
            index = 0
            while index < _REGISTRY_MAX_SUBKEYS:
                try:
                    child_name = registry.EnumKey(root, index)
                except Exception:
                    break
                index += 1
                child = _registry_open(registry, root, child_name)
                if child is None:
                    continue
                try:
                    display_name = _registry_value(registry, child, "DisplayName")
                    kind = _registry_kind(display_name, kinds)
                    if kind is None:
                        continue
                    entry = {
                        "display_name": display_name,
                        "display_version": _registry_value(
                            registry, child, "DisplayVersion"
                        ),
                        "install_location": _registry_value(
                            registry, child, "InstallLocation"
                        ),
                        "display_icon": _registry_value(registry, child, "DisplayIcon"),
                    }
                    for executable in _registry_executables(kind, entry):
                        entries.append({"kind": kind, "executable": executable, **entry})
                finally:
                    _registry_close(registry, child)
        finally:
            _registry_close(registry, root)
    return entries


def _app_path_registry_entries(registry, kinds: list[str]) -> list[dict]:
    entries: list[dict] = []
    for hive_name, subkey in _REGISTRY_APP_PATH_ROOTS:
        hive = getattr(registry, hive_name, None)
        if hive is None:
            continue
        root = _registry_open(registry, hive, subkey)
        if root is None:
            continue
        try:
            for kind in kinds:
                name = HOST_BINARIES[kind]
                for key_name in (f"{name}.exe", name):
                    key = _registry_open(registry, root, key_name)
                    if key is None:
                        continue
                    try:
                        default_value = _registry_value(registry, key, "")
                        install_location = _registry_value(registry, key, "Path")
                    finally:
                        _registry_close(registry, key)
                    executables: list[Path] = []
                    executable = _clean_registry_path(default_value)
                    if executable is not None:
                        try:
                            if executable.is_file() and (
                                executable.suffix.lower() in _EXECUTABLE_SUFFIXES
                                or executable.name.lower() in _executable_names(kind)
                            ):
                                executables.append(executable)
                        except OSError:
                            pass
                    for path in _install_dir_executables(kind, install_location):
                        if _path_key(path) not in {_path_key(item) for item in executables}:
                            executables.append(path)
                    for executable in executables:
                        entries.append(
                            {
                                "kind": kind,
                                "executable": executable,
                                "display_name": key_name,
                                "display_version": "",
                                "install_location": install_location,
                                "display_icon": default_value,
                            }
                        )
        finally:
            _registry_close(registry, root)
    return entries


def _registry_entries(
    kinds: list[str] | None = None, *, registry=None
) -> list[dict]:
    """Return host executables described by read-only uninstall/App Paths metadata.

    ``registry`` is injectable so tests never touch the real registry. Only
    DisplayName, DisplayVersion, InstallLocation, DisplayIcon, and App Paths
    default/Path values are read. No credentials or session data is queried.
    """
    module = registry if registry is not None else winreg
    if module is None:
        return []
    selected = [kind for kind in (kinds if kinds is not None else HOST_BINARIES) if kind in HOST_BINARIES]
    if not selected:
        return []
    entries: list[dict] = []
    try:
        entries.extend(_uninstall_registry_entries(module, selected))
    except Exception:
        pass
    try:
        entries.extend(_app_path_registry_entries(module, selected))
    except Exception:
        pass
    return entries


def _command_line(argv: list[str]) -> list[str]:
    """Windows .cmd/.bat shims need cmd.exe; direct executables stay direct."""
    return winproc.command_line(argv[0], argv[1:])


def _exec_version(executable: str, *, timeout: int = 15) -> tuple[str, str]:
    try:
        proc = subprocess.run(
            _command_line([executable, "--version"]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env={**os.environ, "NO_COLOR": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"cannot run --version: {exc}"
    text = (proc.stdout or "") + (proc.stderr or "")
    version = _VERSION_RE.search(text)
    if proc.returncode != 0 or not version:
        return "", text.strip()[:300] or f"exit {proc.returncode}"
    return version.group(1), text.strip()[:400]


def _candidate_id(kind: str, executable: str, version: str) -> str:
    digest = engine_store.sha256_text(f"{kind}\0{executable}\0{version}")[:10]
    return f"host_{kind.replace('-', '_')}_{digest}"


def _path_binaries(name: str) -> list[Path]:
    found: list[Path] = []
    seen = set()
    names = [f"{name}.exe", f"{name}.cmd", f"{name}.bat", name]
    for raw in os.environ.get("PATH", "").split(os.pathsep):
        if not raw:
            continue
        base = Path(raw)
        for candidate_name in names:
            path = base / candidate_name
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            key = str(resolved).lower()
            if key in seen:
                continue
            seen.add(key)
            if path.is_file():
                found.append(path)
    return found


def _common_bin_dirs() -> list[Path]:
    home = Path.home()
    dirs = [home / ".local" / "bin", home / ".bun" / "bin"]
    for env in ("APPDATA", "LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)"):
        raw = os.environ.get(env)
        if raw:
            base = Path(raw)
            dirs.extend(
                [
                    base,
                    base / "npm",
                    base / "Programs",
                    base / "Programs" / "nodejs",
                    base / "Programs" / "Claude",
                    base / "Programs" / "codex",
                    base / "Programs" / "dsh",
                ]
            )
    for prefix in _package_manager_prefixes():
        dirs.extend([prefix, prefix / "bin", prefix / "node_modules" / ".bin"])
    pnpm_home = os.environ.get("PNPM_HOME")
    if pnpm_home:
        dirs.append(Path(pnpm_home))
    out = []
    seen = set()
    for directory in dirs:
        try:
            key = str(directory.resolve()).lower()
        except OSError:
            key = str(directory).lower()
        if key not in seen and directory.is_dir():
            seen.add(key)
            out.append(directory)
    return out


def _command_output(command: str, args: list[str]) -> str:
    binary = shutil.which(command)
    if not binary:
        return ""
    try:
        proc = subprocess.run(
            winproc.command_line(binary, args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        return (proc.stdout or "").strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _npm_prefix() -> Path | None:
    raw = _command_output("npm", ["config", "get", "prefix"])
    return Path(raw).expanduser() if raw else None


def _package_manager_prefixes() -> list[Path]:
    """Global npm/pnpm/yarn install roots, read without invoking a host binary."""
    out: list[Path] = []
    npm_prefix = _npm_prefix()
    if npm_prefix:
        out.append(npm_prefix)
    pnpm_root = _command_output("pnpm", ["root", "-g"])
    if pnpm_root:
        out.append(Path(pnpm_root).expanduser())
        out.append(Path(pnpm_root).expanduser().parent)
    yarn_bin = _command_output("yarn", ["global", "bin"])
    if yarn_bin:
        out.append(Path(yarn_bin).expanduser())
    return out


def _default_config_dirs(kind: str) -> list[Path]:
    dirs = []
    env = os.environ.get(_CONFIG_ENV[kind])
    if env:
        dirs.append(Path(env).expanduser())
    dirs.extend(DEFAULT_CONFIG_DIR[kind])
    appdata = os.environ.get("APPDATA")
    local = os.environ.get("LOCALAPPDATA")
    if appdata:
        label = {"claude-code": "Claude", "codex": "codex", "dsh": "dsh"}[kind]
        dirs.append(Path(appdata) / label)
    if local:
        label = {"claude-code": "Claude", "codex": "codex", "dsh": "dsh"}[kind]
        dirs.append(Path(local) / "Programs" / label)
        dirs.append(Path(local) / label)
    out = []
    seen = set()
    for directory in dirs:
        key = str(directory).lower()
        if key not in seen:
            seen.add(key)
            out.append(directory)
    return out


def _config_dir(candidate_paths: list[Path]) -> Path:
    for path in candidate_paths:
        if path.is_dir():
            return path
    return candidate_paths[0]


def _config_state(kind: str, config_dir: Path) -> tuple[bool, str]:
    if not config_dir.exists():
        return False, "config_missing"
    if not config_dir.is_dir():
        return False, "config_invalid"
    if not os.access(config_dir, os.W_OK):
        return False, "permission_denied"
    return True, "ok"


def inspect_executable(executable: Path, kind: str, *, config_dir: Path | None = None) -> dict:
    executable = Path(executable).expanduser()
    try:
        executable = executable.resolve()
    except OSError:
        pass
    if not executable.is_file():
        raise ConsumerError("HOST_NOT_FOUND", "没有找到可执行文件。", "手动选择 Harness 安装目录或可执行文件。")
    version, raw = _exec_version(str(executable))
    identity_ok = bool(version) and any(
        token.lower() in raw.lower() for token in HOST_IDENTITY[kind]
    )
    config_dirs = [config_dir] if config_dir else _default_config_dirs(kind)
    config = _config_dir([p for p in config_dirs if p])
    config_ok, config_reason = _config_state(kind, config)
    compatible = False
    reason = ""
    vt = _version_tuple(version)
    if not identity_ok:
        reason = "无法确认这是目标 Harness"
    elif vt is None:
        reason = "无法读取版本"
    elif vt[:2] != COMPATIBLE_MINORS[kind]:
        reason = f"版本 {version} 不在支持矩阵内"
    else:
        compatible = True
        if not config_ok:
            reason = "没有找到配置目录" if config_reason == "config_missing" else "配置目录不可写"
    status = (
        "adaptable"
        if compatible and config_ok
        else "version_unsupported"
        if identity_ok and vt and vt[:2] != COMPATIBLE_MINORS[kind]
        else "identity_unknown"
        if not identity_ok
        else "permission_denied"
        if config_reason == "permission_denied"
        else "config_missing"
    )
    return {
        "id": _candidate_id(kind, str(executable), version),
        "kind": kind,
        "label": HOST_LABELS[kind],
        "version": version,
        "executable": str(executable),
        "install_dir": str(executable.parent),
        "config_dir": str(config) if config else "",
        "config_writable": config_ok,
        "identity": kind if identity_ok else "unknown",
        "compatible": compatible,
        "status": status,
        "reason": reason,
        "integration": "hook" if kind == "claude-code" and config_ok else "wrapper",
        "raw_version": raw,
    }


def _active_install_records(records: list[dict]) -> list[dict]:
    return [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("status") in _ACTIVE_RECORD_STATUSES
        and record.get("host") in HOST_BINARIES
    ]


def _best_install_record(
    records: list[dict], kind: str, executable: object, version: object
) -> dict | None:
    options = [record for record in records if record.get("host") == kind]
    if not options:
        return None
    for record in options:
        if _same_executable(record.get("executable"), executable) and _same_version(
            record.get("version"), version
        ):
            return record
    for record in options:
        if _same_executable(record.get("executable"), executable):
            return record
    return max(options, key=lambda record: _version_tuple(str(record.get("version") or "")) or (0, 0, 0))


def _record_candidate(record: dict) -> dict | None:
    kind = str(record.get("host") or "")
    if kind not in HOST_BINARIES:
        return None
    version = str(record.get("version") or "")
    executable = str(record.get("executable") or "")
    install_dir = str(record.get("install_dir") or "")
    if not install_dir and executable:
        install_dir = str(Path(executable).parent)
    config_value = str(record.get("config_dir") or "")
    config_dir = Path(config_value) if config_value else None
    if config_dir is None:
        config_ok, config_reason = False, "config_missing"
    else:
        config_ok, config_reason = _config_state(kind, config_dir)
    version_ok = (
        (version_tuple := _version_tuple(version)) is not None
        and version_tuple[:2] == COMPATIBLE_MINORS[kind]
    )
    if not version_ok:
        status = "version_unsupported"
        reason = f"版本 {version} 不在支持矩阵内" if version else "无法读取版本"
    elif not config_ok:
        status = "permission_denied" if config_reason == "permission_denied" else "config_missing"
        reason = "配置目录不可写" if config_reason == "permission_denied" else "没有找到配置目录"
    else:
        status = "installed"
        reason = ""
    return {
        "id": record.get("id") or _candidate_id(kind, executable, version),
        "kind": kind,
        "label": HOST_LABELS[kind],
        "version": version,
        "executable": executable,
        "install_dir": install_dir,
        "config_dir": str(config_dir) if config_dir else "",
        "config_writable": config_ok,
        "identity": record.get("identity", kind),
        "compatible": version_ok,
        "status": status,
        "reason": reason,
        "integration": record.get("integration", "wrapper"),
        "installed": True,
        "installed_version": version,
        "upgrade_available": False,
        "repair_required": False,
        "record_id": record.get("id"),
        "sources": ["install_record"],
        "raw_version": version,
    }


def _apply_install_state(candidate: dict, record: dict | None) -> None:
    """Attach stable installed/upgrade/repair fields without changing compatibility."""
    candidate["installed"] = False
    candidate["installed_version"] = str(record.get("version") or "") if record else ""
    candidate["upgrade_available"] = False
    candidate["repair_required"] = False
    if record is None:
        return
    same_executable = _same_executable(candidate.get("executable"), record.get("executable"))
    same_version = _same_version(candidate.get("version"), record.get("version"))
    if same_executable and same_version:
        candidate["installed"] = True
        candidate["record_id"] = record.get("id")
        if not candidate.get("compatible", False):
            return
        if not candidate.get("config_writable", False):
            candidate["repair_required"] = True
            candidate["status"] = "repair_required"
            if not candidate.get("reason"):
                candidate["reason"] = "已安装，但配置目录不可写或缺失"
            return
        candidate["status"] = "installed"
        candidate["reason"] = ""
        return
    installed_version = _version_tuple(str(record.get("version") or ""))
    candidate_version = _version_tuple(str(candidate.get("version") or ""))
    if (
        candidate.get("compatible", False)
        and installed_version is not None
        and candidate_version is not None
        and candidate_version > installed_version
    ):
        candidate["upgrade_available"] = True
        candidate["status"] = "upgrade_available"
        if not candidate.get("reason"):
            candidate["reason"] = (
                f"已安装 {record.get('version')}，发现新版本 {candidate.get('version')}"
            )


def _dedupe_key(candidate: dict) -> tuple[str, str, str]:
    return (
        str(candidate.get("kind") or ""),
        _path_key(candidate.get("executable")),
        _version_text(candidate.get("version")),
    )


def _candidate_sort_key(candidate: dict) -> tuple[str, tuple[int, int, int], str, bool]:
    return (
        str(candidate.get("kind") or ""),
        _version_tuple(str(candidate.get("version") or "")) or (0, 0, 0),
        _version_text(candidate.get("version")),
        bool(candidate.get("installed")),
    )


def _live_host_records(kind: str) -> bool:
    home = Path(os.environ.get("MORPHD_HOME", Path.home() / ".morphd"))
    root = home / "runtime" / "hosts"
    if not root.is_dir():
        return False
    for path in root.glob(f"{kind}-*.json"):
        payload = engine_store.read_json(path)
        if not isinstance(payload, dict):
            continue
        if payload.get("host") != kind:
            continue
        if winproc.pid_alive(payload.get("pid")) or winproc.pid_alive(payload.get("child_pid")):
            return True
        try:
            path.unlink()
        except OSError:
            pass
    return False


def host_running(kind: str) -> bool:
    """Best-effort check that the host is currently running. Never a write guard alone."""
    if _live_host_records(kind):
        return True
    name = HOST_BINARIES[kind].lower()
    try:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        for row in csv.reader(io.StringIO(proc.stdout or "")):
            if row and Path(row[0]).stem.lower() == name:
                return True
        return False
    except Exception:
        return False


class HostManager:
    def __init__(
        self,
        store: ConsumerStore,
        settings: config.Settings | None = None,
        *,
        registry=None,
    ):
        self.store = store
        self.settings = settings
        self.registry = registry
        self.hosts_root = store.home / "hosts"
        self.hosts_root.mkdir(parents=True, exist_ok=True)
        self._discover_cache: tuple[float, list[dict]] | None = None
        try:
            os.chmod(self.hosts_root, 0o700)
        except OSError:
            pass

    # --- install record -------------------------------------------------------
    def _load_install(self) -> dict:
        raw = self.store.read_json(self.store.install_path, default={})
        if not isinstance(raw, dict):
            raw = {}
        raw.setdefault("version", 1)
        raw.setdefault("app_version", APP_VERSION)
        raw.setdefault("platform", "win32-x64")
        raw.setdefault("hosts", [])
        return raw

    def _save_install(self, payload: dict) -> None:
        with self.store.lock("install"):
            self.store.write_json(self.store.install_path, payload)

    def install_records(self) -> list[dict]:
        return list(self._load_install().get("hosts", []))

    def record_for(self, kind: str) -> dict | None:
        for record in self.install_records():
            if record.get("host") == kind:
                return record
        return None

    def installed_hosts(self) -> list[dict]:
        return [r.get("host") for r in self.install_records() if r.get("status") in ("adapted", "beta")]

    # --- discovery ------------------------------------------------------------
    def discover(self, *, kinds: list[str] | None = None, force: bool = False) -> list[dict]:
        if (
            not force
            and kinds is None
            and self._discover_cache is not None
            and time.monotonic() - self._discover_cache[0] < 30
        ):
            return copy.deepcopy(self._discover_cache[1])
        selected = list(HOST_BINARIES) if kinds is None else list(kinds)
        selected = [kind for kind in selected if kind in HOST_BINARIES]
        records = [record for record in self.install_records() if isinstance(record, dict)]
        active_records = _active_install_records(records)
        sources: dict[tuple[str, str], dict] = {}

        def add_source(kind: str, path: Path | str, source: str, registry_version: str = "") -> None:
            try:
                resolved = Path(path).expanduser().resolve()
            except (OSError, ValueError):
                resolved = Path(path)
            key = (kind, _path_key(resolved))
            item = sources.setdefault(
                key, {"path": resolved, "sources": [], "registry_version": ""}
            )
            if source not in item["sources"]:
                item["sources"].append(source)
            if registry_version and not item["registry_version"]:
                item["registry_version"] = registry_version

        if selected:
            common_dirs = _common_bin_dirs()
            for entry in _registry_entries(selected, registry=self.registry):
                add_source(
                    str(entry.get("kind")),
                    entry["executable"],
                    "registry",
                    str(entry.get("display_version") or ""),
                )
            for kind in selected:
                name = HOST_BINARIES[kind]
                for path in _path_binaries(name):
                    add_source(kind, path, "path")
                for directory in common_dirs:
                    for filename in _executable_names(kind):
                        path = directory / filename
                        try:
                            if path.is_file():
                                add_source(kind, path, "common")
                        except OSError:
                            continue

        merged: dict[tuple[str, str, str], dict] = {}
        for (kind, _key), item in sorted(sources.items()):
            path = item["path"]
            record = _best_install_record(
                active_records,
                kind,
                path,
                item["registry_version"],
            )
            config_dir = None
            if record and record.get("config_dir") and _same_executable(
                record.get("executable"), path
            ):
                config_dir = Path(str(record["config_dir"])).expanduser()
            try:
                candidate = inspect_executable(path, kind, config_dir=config_dir)
            except (ConsumerError, OSError):
                continue
            candidate["sources"] = list(item["sources"])
            if item["registry_version"]:
                candidate["registry_version"] = item["registry_version"]
            key = _dedupe_key(candidate)
            existing = merged.get(key)
            if existing is None:
                merged[key] = candidate
            else:
                for source in candidate.get("sources", []):
                    if source not in existing.setdefault("sources", []):
                        existing["sources"].append(source)
                if candidate.get("registry_version") and not existing.get("registry_version"):
                    existing["registry_version"] = candidate["registry_version"]

        for record in active_records:
            candidate = _record_candidate(record)
            if candidate is None:
                continue
            key = _dedupe_key(candidate)
            existing = merged.get(key)
            if existing is None:
                merged[key] = candidate
                continue
            existing.setdefault("record_id", record.get("id"))
            if "install_record" not in existing.setdefault("sources", []):
                existing["sources"].append("install_record")

        candidates = list(merged.values())
        for candidate in candidates:
            record = _best_install_record(
                active_records,
                str(candidate.get("kind") or ""),
                candidate.get("executable"),
                candidate.get("version"),
            )
            _apply_install_state(candidate, record)
        candidates.sort(key=_candidate_sort_key, reverse=True)
        if selected == list(HOST_BINARIES):
            self._discover_cache = (time.monotonic(), copy.deepcopy(candidates))
        return candidates

    def validate_manual(self, kind: str, *, install_path: str, config_path: str | None = None,
                        project_root: str | None = None) -> dict:
        if kind not in HOST_BINARIES:
            raise ConsumerError("HOST_UNSUPPORTED", "未知宿主。", "选择 dsh、Claude Code 或 Codex。")
        if not str(install_path or "").strip():
            raise ConsumerError(
                "INVALID_REQUEST",
                "请选择 Harness 安装目录或可执行文件。",
                "重新选择后重试；不会使用当前目录作为默认值。",
            )
        path = Path(install_path).expanduser().resolve()
        executable: Path | None = None
        if path.is_file():
            executable = path
        elif path.is_dir():
            names = [
                f"{HOST_BINARIES[kind]}.exe",
                f"{HOST_BINARIES[kind]}.cmd",
                HOST_BINARIES[kind],
            ]
            for candidate_name in names:
                for base in (path, path / "bin", path / "node_modules" / ".bin"):
                    candidate = base / candidate_name
                    if candidate.is_file():
                        executable = candidate
                        break
                if executable:
                    break
        if executable is None:
            raise ConsumerError(
                "HOST_NOT_FOUND",
                "这个文件夹里没有找到目标 Harness。",
                "选择包含 dsh/claude/codex 可执行文件的安装目录。",
            )
        manual_config = None
        if config_path:
            manual_config = Path(config_path).expanduser().resolve()
            if not manual_config.is_dir():
                raise ConsumerError("INVALID_REQUEST", "配置目录不存在。", "重新选择配置目录。")
            if not self._looks_like_config(kind, manual_config):
                raise ConsumerError(
                    "INVALID_REQUEST",
                    "这个文件夹不像目标 Harness 的配置目录。",
                    "选择 ~/.dsh、~/.claude 或 ~/.codex，或留空。",
                )
            if not os.access(manual_config, os.W_OK):
                raise ConsumerError("FORBIDDEN", "当前用户无法写入该配置目录。", "换一个目录或以用户级方式重装宿主。")
        candidate = inspect_executable(executable, kind, config_dir=manual_config)
        if project_root:
            project = Path(project_root).expanduser().resolve()
            if not project.is_dir():
                raise ConsumerError("INVALID_REQUEST", "项目目录不存在。", "重新选择项目文件夹。")
            candidate["project_root"] = str(project)
        candidate["manual"] = True
        return candidate

    @staticmethod
    def _looks_like_config(kind: str, path: Path) -> bool:
        if kind == "dsh":
            return (
                (path / "profiles").is_dir()
                or (path / "sessions").is_dir()
                or (path / "config.toml").is_file()
            )
        if kind == "claude-code":
            return (
                (path / "settings.json").is_file()
                or (path / "settings.local.json").is_file()
                or (path / "projects").is_dir()
            )
        return (path / "config.toml").is_file() or (path / "sessions").is_dir()

    # --- adaptation -----------------------------------------------------------
    @staticmethod
    def _engine_command() -> tuple[str, list[str]]:
        engine = os.environ.get("MORPHD_ENGINE_BIN", "").strip()
        if engine:
            return engine, []
        if getattr(sys, "frozen", False):
            return sys.executable, []
        return sys.executable, ["-m", "morphd"]

    def _wrapper_cmd(self, kind: str, executable: str) -> str:
        command, prefix = self._engine_command()
        pieces = [
            f'"{command}"',
            *prefix,
            "host",
            "run",
            "--host",
            kind,
            "--binary",
            f'"{executable}"',
            "--home",
            f'"{self.store.home}"',
            "--",
            "%*",
        ]
        return "@echo off\r\n" + " ".join(pieces) + "\r\n"

    def adapt(self, candidate: dict, *, project_root: str | None = None, register_plugin: bool = False) -> dict:
        kind = str(candidate.get("kind") or "")
        if kind not in HOST_BINARIES:
            raise ConsumerError("HOST_UNSUPPORTED", "未知宿主。", "选择 dsh、Claude Code 或 Codex。")
        if not candidate.get("compatible", False):
            raise ConsumerError(
                "HOST_UNSUPPORTED",
                candidate.get("reason") or "这个 Harness 版本不在支持范围内。",
                "查看兼容矩阵，或升级宿主。",
            )
        executable = Path(str(candidate.get("executable") or "")).resolve()
        if not executable.is_file():
            raise ConsumerError("HOST_NOT_FOUND", "Harness 可执行文件不存在。", "重新发现或手动指定。")
        if host_running(kind):
            raise ConsumerError(
                "HOST_RUNNING",
                "宿主正在运行，修改配置可能不会生效。",
                "关闭宿主后重试，或重启宿主以启用 harness2-morphd。",
            )
        existing = self.record_for(kind)
        if (
            existing
            and existing.get("executable") == str(executable)
            and self._record_healthy(existing)
        ):
            updated = False
            if project_root:
                resolved_project = str(Path(project_root).expanduser().resolve())
                if existing.get("project_root") != resolved_project:
                    existing["project_root"] = resolved_project
                    updated = True
            if candidate.get("version") and existing.get("version") != candidate.get("version"):
                existing["version"] = candidate.get("version")
                updated = True
            if updated:
                install = self._load_install()
                install["hosts"] = [
                    existing if item.get("host") == kind else item
                    for item in install.get("hosts", [])
                ]
                self._save_install(install)
            return existing
        if existing:
            # Re-adapting (repair, version switch or a moved executable) starts from the
            # original host state so backups never capture a previously installed hook.
            self._rollback(existing.get("backups", []), existing.get("created", []))
            install = self._load_install()
            install["hosts"] = [
                item for item in install.get("hosts", []) if item.get("host") != kind
            ]
            self._save_install(install)
            shutil.rmtree(self.hosts_root / kind, ignore_errors=True)
        config_dir = Path(str(candidate.get("config_dir"))).expanduser() if candidate.get("config_dir") else None
        project = Path(project_root).expanduser().resolve() if project_root else None
        host_root = self.hosts_root / kind
        host_root.mkdir(parents=True, exist_ok=True)
        bin_dir = host_root / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        backups: list[dict] = []
        created: list[str] = []
        integration = "wrapper"
        try:
            wrapper = bin_dir / f"{HOST_BINARIES[kind]}.cmd"
            engine_store.atomic_write_text(wrapper, self._wrapper_cmd(kind, str(executable)))
            created.append(str(wrapper))
            plugin_note = host_root / "README.txt"
            engine_store.atomic_write_text(
                plugin_note,
                (
                    "morphd host bridge\n"
                    f"host: {kind}\n"
                    f"real executable: {executable}\n"
                    f"wrapper: {wrapper}\n\n"
                    "启动宿主时使用上面的 wrapper，harness2-morphd 的本地 UI 与任务运行器会随宿主启停。\n"
                    "原生插件/hook 不可用时，会话结束分析会在下次启动时从检查点继续。\n"
                ),
            )
            created.append(str(plugin_note))
            hook_path = ""
            if kind == "claude-code":
                hook_dir = (project / ".claude") if project else config_dir
                hook_dir_existed = bool(hook_dir and hook_dir.is_dir())
                if hook_dir is not None:
                    try:
                        hook_dir.mkdir(parents=True, exist_ok=True)
                    except OSError:
                        pass
                if hook_dir and not hook_dir_existed and hook_dir.is_dir():
                    created.append(str(hook_dir))
                if hook_dir and hook_dir.is_dir() and os.access(hook_dir, os.W_OK):
                    hook_file = hook_dir / "settings.local.json"
                    hook_record = self._install_claude_hook(hook_file)
                    backups.extend(hook_record["backups"])
                    created.extend(hook_record["created"])
                    hook_path = str(hook_file)
                    integration = "hook"
            status = "adapted" if integration == "hook" else "beta"
            record = {
                "id": candidate.get("id") or _candidate_id(kind, str(executable), candidate.get("version", "")),
                "host": kind,
                "version": candidate.get("version", ""),
                "executable": str(executable),
                "install_dir": candidate.get("install_dir") or str(executable.parent),
                "config_dir": str(config_dir) if config_dir else "",
                "project_root": str(project) if project else str(candidate.get("project_root") or ""),
                "adapter_version": APP_VERSION,
                "integration": integration,
                "wrapper": str(wrapper),
                "hooks": [hook_path] if hook_path else [],
                "identity": candidate.get("identity", kind),
                "backups": backups,
                "created": created,
                "status": status,
                "installed_at": engine_store.utc_now(),
                "notes": (
                    "通过 wrapper 启动宿主；原生 hook 不可用时自动分析会在下次启动续跑。"
                    if integration == "wrapper"
                    else "已安装 SessionEnd hook；请重启宿主生效。"
                ),
            }
            probe = self._probe(record)
            if not probe["ok"]:
                raise ConsumerError(
                    "ADAPT_FAILED",
                    f"适配自检失败：{probe['reason']}",
                    "已回滚，没有留下修改。",
                )
            install = self._load_install()
            install["hosts"] = [
                record if item.get("host") == kind else item for item in install.get("hosts", [])
            ]
            if not any(item.get("host") == kind for item in install.get("hosts", [])):
                install["hosts"].append(record)
            install["app_version"] = APP_VERSION
            install["installed_at"] = engine_store.utc_now()
            self._save_install(install)
            self._discover_cache = None
            self.store.append_log("host_adapted", host=kind, version=record["version"], integration=integration)
            return record
        except BaseException:
            self._rollback(backups, created)
            raise

    def _probe(self, record: dict) -> dict:
        wrapper = Path(record["wrapper"])
        if not wrapper.is_file():
            return {"ok": False, "reason": "wrapper 未写入"}
        engine, prefix = self._engine_command()
        try:
            proc = subprocess.run(
                [engine, *prefix, "version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "reason": f"引擎自检无法启动：{exc}"}
        if proc.returncode != 0:
            return {"ok": False, "reason": "引擎版本自检失败"}
        return {"ok": True, "reason": ""}

    def _record_healthy(self, record: dict) -> bool:
        wrapper = Path(str(record.get("wrapper") or ""))
        if not wrapper.is_file():
            return False
        try:
            wrapper_text = wrapper.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        if "host" not in wrapper_text or "run" not in wrapper_text:
            return False
        for hook in record.get("hooks", []) or []:
            path = Path(str(hook))
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                if "host" not in text or "event" not in text:
                    return False
            except OSError:
                return False
        return True

    def _install_claude_hook(self, hook_file: Path) -> dict:
        backups = []
        created = []
        original = hook_file.read_bytes() if hook_file.is_file() else None
        backup_path = ""
        if original is not None:
            backup_dir = self.hosts_root / "claude-code" / "backups" / engine_store.utc_stamp()
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / hook_file.name
            engine_store.atomic_write_bytes(backup, original)
            backup_path = str(backup)
            backups.append(
                {
                    "path": str(hook_file),
                    "backup_path": backup_path,
                    "sha256": engine_store.sha256_bytes(original),
                }
            )
        payload = {}
        if original is not None:
            try:
                payload = json.loads(original.decode("utf-8"))
            except ValueError as exc:
                raise ConsumerError(
                    "ADAPT_FAILED",
                    "宿主 hook 配置不是有效 JSON，未做修改。",
                    "修复或备份该配置后重试。",
                ) from exc
        if not isinstance(payload, dict):
            raise ConsumerError(
                "ADAPT_FAILED",
                "宿主 hook 配置结构不符合预期，未做修改。",
                "备份该配置后手动检查，或选择手动模式。",
            )
        hooks = payload.setdefault("hooks", {})
        engine, prefix = self._engine_command()
        base = [
            *prefix,
            "host",
            "event",
            "--host",
            "claude-code",
            "--home",
            f'"{self.store.home}"',
        ]
        for hook_name, extra in (("SessionStart", ["--event", "session-start"]), ("SessionEnd", [])):
            entries = hooks.setdefault(hook_name, [])
            if not isinstance(entries, list):
                entries = []
                hooks[hook_name] = entries
            command = f'"{engine}" ' + " ".join([*base, *extra, "--stdin"])
            entry = {"hooks": [{"type": "command", "command": command, "timeout": 60}]}
            if not any(
                "host" in json.dumps(item) and "event" in json.dumps(item)
                for item in entries
                if isinstance(item, dict)
            ):
                entries.append(entry)
        engine_store.atomic_write_json(hook_file, payload)
        try:
            os.chmod(hook_file, 0o600)
        except OSError:
            pass
        if original is None:
            created.append(str(hook_file))
        self.store.append_log("claude_hook_installed", path=str(hook_file), backup=backup_path)
        return {"backups": backups, "created": created}

    def uninstall(self, kind: str) -> dict:
        record = self.record_for(kind)
        if record is None:
            raise ConsumerError("HOST_NOT_FOUND", "这个 Harness 没有安装记录。", "刷新宿主页。")
        self._rollback(record.get("backups", []), record.get("created", []))
        shutil.rmtree(self.hosts_root / kind, ignore_errors=True)
        install = self._load_install()
        install["hosts"] = [item for item in install.get("hosts", []) if item.get("host") != kind]
        self._save_install(install)
        self._discover_cache = None
        self.store.append_log("host_uninstalled", host=kind)
        return {"ok": True, "host": kind, "backups_restored": len(record.get("backups", []))}

    @staticmethod
    def _rollback(backups: list[dict], created: list[str]) -> None:
        for item in reversed(backups):
            path = Path(str(item.get("path")))
            backup = Path(str(item.get("backup_path") or ""))
            try:
                if backup:
                    if not backup.is_file():
                        # A missing backup is an integrity failure; never delete the
                        # user's file just because we cannot prove it was ours.
                        continue
                    path.parent.mkdir(parents=True, exist_ok=True)
                    engine_store.atomic_write_bytes(path, backup.read_bytes())
                elif path.exists():
                    path.unlink()
            except OSError:
                continue
        for raw in reversed(created):
            path = Path(raw)
            try:
                if path.is_file() or path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()  # only removes a directory we created and that is empty
            except OSError:
                continue

    # --- harness view ---------------------------------------------------------
    def harness_root(self, kind: str, *, project_root: str | None = None) -> Path:
        record = self.record_for(kind)
        if record is None:
            raise ConsumerError("HOST_NOT_FOUND", "还没有为这个 Harness 安装 harness2-morphd。", "先在宿主页完成适配。")
        root = Path(project_root or record.get("project_root") or "").expanduser()
        if not root.is_absolute() or not root.is_dir():
            config_dir = Path(record.get("config_dir") or "")
            if kind == "dsh" and (config_dir / "profiles").is_dir():
                profiles = sorted(
                    (p for p in (config_dir / "profiles").iterdir() if p.is_dir()),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if profiles:
                    return profiles[0]
            return config_dir if config_dir.is_dir() else self.store.home
        return root

    def _mapped_paths(self, kind: str, project_root: Path) -> dict[str, Path]:
        record = self.record_for(kind) or {}
        config_dir = Path(str(record.get("config_dir") or "")) if record.get("config_dir") else None
        if kind == "dsh":
            return {
                "AGENTS.md": project_root / "AGENTS.md",
                "scripts": project_root / "scripts",
                "skills": project_root / "skills",
            }
        if kind == "claude-code":
            # Only declared surfaces are read or written. Claude's settings, hooks and
            # MCP configuration are deliberately excluded even though they live under
            # ``.claude``; a candidate must never rewrite them.
            config_root = (
                config_dir is not None
                and not str(record.get("project_root") or "").strip()
                and project_root.resolve() == config_dir.resolve()
            )
            claude_base = project_root if config_root else project_root / ".claude"
            return {
                "CLAUDE.md": project_root / "CLAUDE.md",
                "scripts": project_root / "scripts",
                ".claude/skills": claude_base / "skills",
                ".claude/agents": claude_base / "agents",
            }
        return {
            "AGENTS.md": project_root / "AGENTS.md",
            "scripts": project_root / "scripts",
            ".agents": project_root / ".agents",
        }

    def read_harness_tree(
        self, kind: str, *, project_root: str | None = None, include_base: bool = False
    ) -> dict[str, str]:
        root = self.harness_root(kind, project_root=project_root)
        paths = self._mapped_paths(kind, root)
        tree: dict[str, str] = {}
        instruction = paths.get(_PROJECT_FILES[kind])
        if instruction and instruction.is_file():
            tree[_PROJECT_FILES[kind]] = instruction.read_text(encoding="utf-8", errors="replace")
        elif include_base:
            base = config.packaged_base_harness(kind) / _PROJECT_FILES[kind]
            if base.is_file():
                tree[_PROJECT_FILES[kind]] = base.read_text(encoding="utf-8", errors="replace")
        for prefix, directory in paths.items():
            if prefix in ("AGENTS.md", "CLAUDE.md") or not directory.is_dir():
                continue
            for path in engine_store.iter_files(directory):
                rel = path.relative_to(directory).as_posix()
                if prefix == "skills":
                    key = f"skills/{rel}"
                elif prefix.startswith("."):
                    key = f"{prefix}/{rel}"
                else:
                    key = f"scripts/{rel}" if prefix == "scripts" else rel
                try:
                    data = path.read_bytes()
                except OSError:
                    continue
                if len(data) > 1_000_000 or b"\x00" in data[:4096]:
                    continue
                tree[key] = data.decode("utf-8", errors="replace")
        return tree

    def target_for(self, kind: str, rel: str, *, project_root: str | None = None) -> Path:
        root = self.harness_root(kind, project_root=project_root)
        paths = self._mapped_paths(kind, root)
        instruction = _PROJECT_FILES[kind]
        if rel == instruction:
            base, target = Path(paths[instruction]).parent, paths[instruction]
        elif rel.startswith("skills/"):
            base, target = paths["skills"], paths["skills"] / rel[len("skills/") :]
        elif rel.startswith(".claude/skills/"):
            base = paths[".claude/skills"]
            target = base / rel[len(".claude/skills/") :]
        elif rel.startswith(".claude/agents/"):
            base = paths[".claude/agents"]
            target = base / rel[len(".claude/agents/") :]
        elif rel.startswith(".agents/"):
            base, target = paths[".agents"], paths[".agents"] / rel[len(".agents/") :]
        elif rel.startswith("scripts/"):
            base, target = paths["scripts"], paths["scripts"] / rel[len("scripts/") :]
        else:
            raise ConsumerError("CANDIDATE_UNSAFE", f"文件不在声明的落点内：{rel}", "检查候选。")
        try:
            target.resolve().relative_to(base.resolve())
        except ValueError as exc:
            raise ConsumerError(
                "CANDIDATE_UNSAFE",
                f"{rel} 解析后越过允许的 harness 目录（符号链接或路径穿越）。",
                "移除符号链接后重试。",
            ) from exc
        junction = getattr(target, "is_junction", lambda: False)
        if (
            target.is_symlink()
            or junction()
            or any(parent.is_symlink() for parent in target.parents if parent != target)
        ):
            raise ConsumerError("CANDIDATE_UNSAFE", f"{rel} 是符号链接/联接点或被其目录包含。", "移除符号链接或联接点后重试。")
        return target

    def apply_tree(self, kind: str, tree: dict[str, str], *, project_root: str | None = None) -> list[str]:
        """Write only the supplied files; callers pass the changed/added set, never a
        snapshot of the whole harness (which could include settings or hooks)."""
        written = []
        for rel, text in tree.items():
            target = self.target_for(kind, rel, project_root=project_root)
            engine_store.atomic_write_text(target, text)
            written.append(rel)
        return written
