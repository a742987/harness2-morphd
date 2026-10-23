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


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(value or "")
    if not match:
        return None
    parts = tuple(int(part) for part in match.group(1).split("."))
    return parts  # type: ignore[return-value]


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
                ]
            )
    npm_prefix = _npm_prefix()
    if npm_prefix:
        dirs.extend([npm_prefix, npm_prefix / "node_modules" / ".bin"])
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


def _npm_prefix() -> Path | None:
    npm = shutil.which("npm")
    if not npm:
        return None
    try:
        proc = subprocess.run(
            winproc.command_line(npm, ["config", "get", "prefix"]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
        raw = (proc.stdout or "").strip()
        return Path(raw).expanduser() if proc.returncode == 0 and raw else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _default_config_dirs(kind: str) -> list[Path]:
    dirs = []
    env = os.environ.get(_CONFIG_ENV[kind])
    if env:
        dirs.append(Path(env).expanduser())
    dirs.extend(DEFAULT_CONFIG_DIR[kind])
    appdata = os.environ.get("APPDATA")
    local = os.environ.get("LOCALAPPDATA")
    if appdata:
        dirs.append(Path(appdata) / ("Claude" if kind == "claude-code" else kind))
    if local and kind == "claude-code":
        dirs.append(Path(local) / "Programs" / "Claude")
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


def host_running(kind: str) -> bool:
    """Best-effort check that the host is currently running. Never a write guard alone."""
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
    def __init__(self, store: ConsumerStore, settings: config.Settings | None = None):
        self.store = store
        self.settings = settings
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
        kinds = kinds or list(HOST_BINARIES)
        candidates: list[dict] = []
        seen = set()
        for kind in kinds:
            name = HOST_BINARIES[kind]
            paths = _path_binaries(name)
            for directory in _common_bin_dirs():
                for suffix in ("exe", "cmd", "bat", ""):
                    path = directory / f"{name}.{suffix}" if suffix else directory / name
                    if path.is_file():
                        paths.append(path)
            unique: list[Path] = []
            for path in paths:
                try:
                    key = str(path.resolve()).lower()
                except OSError:
                    key = str(path).lower()
                if key not in seen:
                    seen.add(key)
                    unique.append(path)
            for path in unique:
                try:
                    candidates.append(inspect_executable(path, kind))
                except ConsumerError:
                    continue
        for record in self.install_records():
            candidates.append(
                {
                    "id": record.get("id"),
                    "kind": record.get("host"),
                    "label": HOST_LABELS.get(record.get("host"), record.get("host")),
                    "version": record.get("version"),
                    "executable": record.get("executable"),
                    "install_dir": record.get("install_dir"),
                    "config_dir": record.get("config_dir"),
                    "identity": record.get("identity", record.get("host")),
                    "compatible": True,
                    "status": "installed",
                    "reason": "",
                    "integration": record.get("integration", "wrapper"),
                    "installed": True,
                    "record_id": record.get("id"),
                }
            )
        candidates.sort(key=lambda item: (item.get("kind", ""), item.get("version", "")), reverse=True)
        if kinds == list(HOST_BINARIES):
            self._discover_cache = (time.monotonic(), copy.deepcopy(candidates))
        return candidates

    def validate_manual(self, kind: str, *, install_path: str, config_path: str | None = None,
                        project_root: str | None = None) -> dict:
        if kind not in HOST_BINARIES:
            raise ConsumerError("HOST_UNSUPPORTED", "未知宿主。", "选择 dsh、Claude Code 或 Codex。")
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
            return (path / "profiles").is_dir() or any(path.glob("*.yaml")) or any(path.glob("*.yml"))
        if kind == "claude-code":
            return path.name == ".claude" or (path / "settings.json").exists() or (path / "projects").is_dir()
        return (
            (path / "config.toml").exists()
            or (path / "sessions").is_dir()
            or (path / "auth.json").exists()
        )

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
        pieces = [f'"{command}"', *prefix, "host", "run", "--host", kind, "--binary", f'"{executable}"', "--", "%*"]
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
        if existing and existing.get("executable") == str(executable):
            return existing
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
            if kind == "claude-code" and config_dir and config_dir.is_dir() and os.access(config_dir, os.W_OK):
                hook_file = config_dir / "settings.local.json"
                hook_record = self._install_claude_hook(hook_file)
                backups.extend(hook_record["backups"])
                created.extend(hook_record["created"])
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
        except Exception:
            self._rollback(backups, created)
            raise

    def _probe(self, record: dict) -> dict:
        wrapper = Path(record["wrapper"])
        if not wrapper.is_file():
            return {"ok": False, "reason": "wrapper 未写入"}
        return {"ok": True, "reason": ""}

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
            except ValueError:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        hooks = payload.setdefault("hooks", {})
        engine, prefix = self._engine_command()
        base = [*prefix, "host", "event", "--host", "claude-code"]
        for hook_name, extra in (("SessionStart", ["--event", "session-start"]), ("SessionEnd", [])):
            entries = hooks.setdefault(hook_name, [])
            if not isinstance(entries, list):
                entries = []
                hooks[hook_name] = entries
            command = f'"{engine}" ' + " ".join([*base, *extra, "--stdin"])
            entry = {"hooks": [{"type": "command", "command": command, "timeout": 60}]}
            if not any(
                "morphd host event" in json.dumps(item) for item in entries if isinstance(item, dict)
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
                if backup and backup.is_file():
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
                    shutil.rmtree(path, ignore_errors=True)
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
                "skills": (config_dir / "skills") if config_dir else project_root / "skills",
            }
        if kind == "claude-code":
            return {
                "CLAUDE.md": project_root / "CLAUDE.md",
                "scripts": project_root / "scripts",
                ".claude": project_root / ".claude",
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
        elif rel.startswith(".claude/"):
            base, target = paths[".claude"], paths[".claude"] / rel[len(".claude/") :]
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
        written = []
        for rel, text in tree.items():
            target = self.target_for(kind, rel, project_root=project_root)
            engine_store.atomic_write_text(target, text)
            written.append(rel)
        return written
