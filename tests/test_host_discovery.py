from __future__ import annotations

import os
from pathlib import Path

import pytest

from morphd.consumer import hosts as hosts_module
from morphd.consumer.errors import ConsumerError
from morphd.consumer.hosts import HOST_BINARIES, HostManager
from morphd.consumer.store import ConsumerStore

UNINSTALL_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
APP_PATH_ROOT = r"Software\Microsoft\Windows\CurrentVersion\App Paths"
CONFIG_ENV = {"dsh": "DSH_HOME", "claude-code": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}


class FakeKey:
    def __init__(self, hive: str, path: str):
        self.hive = hive
        self.path = path


class FakeWinreg:
    """Minimal read-only winreg surface backed by an in-memory tree."""

    HKEY_CURRENT_USER = "HKEY_CURRENT_USER"
    HKEY_LOCAL_MACHINE = "HKEY_LOCAL_MACHINE"
    KEY_READ = 0x20019

    def __init__(self):
        self.nodes: dict[tuple[str, str], dict[str, object]] = {}
        self.open_calls: list[tuple[str, str, int]] = []
        self.fail_open = False
        self.fail_query = False

    @staticmethod
    def _path(path: str) -> str:
        return path.strip("\\").lower()

    def add(self, hive: str, path: str, values: dict[str, object]) -> None:
        parts = self._path(path).split("\\")
        for index in range(len(parts)):
            self.nodes.setdefault((hive, "\\".join(parts[: index + 1])), {})
        self.nodes[(hive, self._path(path))] = dict(values)

    def add_uninstall(self, hive: str, name: str, values: dict[str, object]) -> None:
        self.add(hive, f"{UNINSTALL_ROOT}\\{name}", values)

    def add_app_path(self, hive: str, name: str, values: dict[str, object]) -> None:
        self.add(hive, f"{APP_PATH_ROOT}\\{name}", values)

    def _exists(self, hive: str, path: str) -> bool:
        prefix = f"{path}\\" if path else ""
        return any(
            node_hive == hive and (node_path == path or node_path.startswith(prefix))
            for node_hive, node_path in self.nodes
        )

    def OpenKey(self, root, subkey: str, reserved: int = 0, access: int = 0):  # noqa: N802
        if self.fail_open:
            raise PermissionError("registry denied")
        if isinstance(root, FakeKey):
            hive, base = root.hive, root.path
        else:
            hive, base = str(root), ""
        path = self._path(f"{base}\\{subkey}" if base else subkey)
        if not self._exists(hive, path):
            raise FileNotFoundError(path)
        self.open_calls.append((hive, path, access))
        self.nodes.setdefault((hive, path), {})
        return FakeKey(hive, path)

    def EnumKey(self, key: FakeKey, index: int):  # noqa: N802
        prefix = f"{key.path}\\" if key.path else ""
        children = sorted(
            {
                path[len(prefix) :].split("\\")[0]
                for hive, path in self.nodes
                if hive == key.hive and path.startswith(prefix) and path != key.path
            }
        )
        if index >= len(children):
            raise OSError("no more keys")
        return children[index]

    def QueryValueEx(self, key: FakeKey, name: str):  # noqa: N802
        if self.fail_query:
            raise PermissionError("registry denied")
        for value_name, value in self.nodes.get((key.hive, key.path), {}).items():
            if value_name.lower() == name.lower():
                return value, 1
        raise FileNotFoundError(name)

    def CloseKey(self, key: FakeKey) -> None:  # noqa: N802
        return None


def _isolate(monkeypatch, tmp_path: Path) -> None:
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setenv("MORPHD_HOME", str(tmp_path / "morphd-home"))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    monkeypatch.setattr(hosts_module, "_common_bin_dirs", lambda: [])
    # Never consult a real running host in discovery/adaptation tests.
    monkeypatch.setattr(hosts_module, "host_running", lambda kind: False)


def _fake_host(path: Path, output: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (
            "@echo off\r\n"
            f'if "%1"=="--version" (echo {output} & exit /b 0)\r\n'
            "exit /b 0\r\n"
        ).encode("utf-8")
    )
    return path


def _set_config(monkeypatch, kind: str, config_dir: Path) -> Path:
    config_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(CONFIG_ENV[kind], str(config_dir))
    return config_dir


def _store(tmp_path: Path) -> ConsumerStore:
    return ConsumerStore(tmp_path / "home")


def _record(
    kind: str,
    executable: Path,
    config_dir: Path,
    *,
    version: str = "0.2.1",
    status: str = "adapted",
) -> dict:
    return {
        "id": f"host_{kind.replace('-', '_')}_record",
        "host": kind,
        "version": version,
        "executable": str(executable),
        "install_dir": str(executable.parent),
        "config_dir": str(config_dir),
        "adapter_version": "0.5.0",
        "integration": "wrapper",
        "wrapper": str(executable.parent / "wrapper.cmd"),
        "backups": [],
        "created": [],
        "status": status,
        "installed_at": "2026-10-23T00:00:00+00:00",
        "identity": kind,
    }


def _write_records(store: ConsumerStore, records: list[dict]) -> None:
    store.write_json(
        store.install_path,
        {
            "version": 1,
            "app_version": "0.5.0",
            "platform": "win32-x64",
            "hosts": records,
        },
    )


def _dsh_candidates(manager: HostManager) -> list[dict]:
    return [c for c in manager.discover(force=True) if c.get("kind") == "dsh"]


def _snapshot(root: Path) -> list[tuple[str, str, bytes]]:
    entries: list[tuple[str, str, bytes]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            entries.append((relative, "dir", b""))
        else:
            entries.append((relative, "file", path.read_bytes()))
    return entries


def test_registry_uninstall_and_app_paths_discovery(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    executable = _fake_host(tmp_path / "registry" / "dsh.cmd", "dsh 0.2.1-registry")
    config = _set_config(monkeypatch, "dsh", tmp_path / "dsh-config")
    registry = FakeWinreg()
    registry.add_uninstall(
        registry.HKEY_CURRENT_USER,
        "dsh",
        {
            "DisplayName": "dsh",
            "DisplayVersion": "0.2.1",
            "InstallLocation": str(executable.parent),
            "DisplayIcon": f"{executable},0",
        },
    )
    registry.add_app_path(
        registry.HKEY_LOCAL_MACHINE,
        "dsh.exe",
        {"": str(executable), "Path": str(executable.parent)},
    )
    manager = HostManager(_store(tmp_path), registry=registry)

    candidates = _dsh_candidates(manager)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert os.path.normcase(candidate["executable"]) == os.path.normcase(str(executable.resolve()))
    assert candidate["version"] == "0.2.1"
    assert candidate["identity"] == "dsh"
    assert candidate["compatible"] is True
    assert candidate["status"] == "adaptable"
    assert candidate["config_dir"] == str(config)
    assert "registry" in candidate["sources"]
    assert "common" not in candidate["sources"]
    assert candidate["registry_version"] == "0.2.1"
    assert candidate["installed"] is False
    assert candidate["installed_version"] == ""
    assert candidate["upgrade_available"] is False
    assert candidate["repair_required"] is False
    assert registry.open_calls
    assert all(access & registry.KEY_READ for _hive, _path, access in registry.open_calls)


def test_running_host_blocks_adapt_without_writes(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    executable = _fake_host(tmp_path / "install" / "dsh.cmd", "dsh 0.2.1")
    monkeypatch.setenv("PATH", str(executable.parent))
    _set_config(monkeypatch, "dsh", tmp_path / "dsh-config")
    manager = HostManager(_store(tmp_path))
    candidate = _dsh_candidates(manager)[0]
    assert candidate["compatible"] is True
    monkeypatch.setattr(hosts_module, "host_running", lambda kind: True)
    before = _snapshot(tmp_path / "home")

    with pytest.raises(ConsumerError) as excinfo:
        manager.adapt(candidate, project_root=str(tmp_path / "project"))

    assert excinfo.value.code == "HOST_RUNNING"
    assert "关闭宿主" in excinfo.value.action
    assert manager.record_for("dsh") is None
    assert _snapshot(tmp_path / "home") == before


def test_registry_identity_is_enforced(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    impostor = _fake_host(tmp_path / "impostor" / "dsh.cmd", "not-a-host 0.2.1")
    _set_config(monkeypatch, "dsh", tmp_path / "dsh-config")
    registry = FakeWinreg()
    registry.add_uninstall(
        registry.HKEY_CURRENT_USER,
        "dsh-impostor",
        {
            "DisplayName": "dsh",
            "DisplayVersion": "0.2.1",
            "InstallLocation": str(impostor.parent),
        },
    )
    manager = HostManager(_store(tmp_path), registry=registry)

    [candidate] = _dsh_candidates(manager)

    assert candidate["identity"] == "unknown"
    assert candidate["compatible"] is False
    assert candidate["status"] == "identity_unknown"
    assert candidate["installed"] is False


def test_dedupe_and_multiple_versions_are_preserved(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    first = _fake_host(tmp_path / "bin-one" / "dsh.cmd", "dsh 0.2.1-one")
    second = _fake_host(tmp_path / "bin-two" / "dsh.cmd", "dsh 0.2.2-two")
    _set_config(monkeypatch, "dsh", tmp_path / "dsh-config")
    monkeypatch.setenv("PATH", f"{first.parent}{os.pathsep}{second.parent}")
    registry = FakeWinreg()
    registry.add_uninstall(
        registry.HKEY_CURRENT_USER,
        "dsh",
        {
            "DisplayName": "dsh",
            "DisplayVersion": "0.2.1",
            "InstallLocation": str(first.parent),
        },
    )
    registry.add_app_path(
        registry.HKEY_CURRENT_USER,
        "dsh.exe",
        {"": str(first)},
    )
    manager = HostManager(_store(tmp_path), registry=registry)

    candidates = _dsh_candidates(manager)

    assert [candidate["version"] for candidate in candidates] == ["0.2.2", "0.2.1"]
    keys = {
        (os.path.normcase(candidate["executable"]), candidate["version"]) for candidate in candidates
    }
    assert len(keys) == 2
    assert len(candidates) == 2
    by_version = {candidate["version"]: candidate for candidate in candidates}
    assert "path" in by_version["0.2.1"]["sources"]
    assert "registry" in by_version["0.2.1"]["sources"]
    assert by_version["0.2.2"]["sources"] == ["path"]
    assert all(candidate["compatible"] for candidate in candidates)


def test_installed_upgrade_and_repair_states(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    old = _fake_host(tmp_path / "bin-old" / "dsh.cmd", "dsh 0.2.1-old")
    new = _fake_host(tmp_path / "bin-new" / "dsh.cmd", "dsh 0.2.2-new")
    config = _set_config(monkeypatch, "dsh", tmp_path / "dsh-config")
    monkeypatch.setenv("PATH", f"{old.parent}{os.pathsep}{new.parent}")
    store = _store(tmp_path)
    _write_records(store, [_record("dsh", old, config)])
    manager = HostManager(store, registry=FakeWinreg())

    candidates = _dsh_candidates(manager)
    by_version = {candidate["version"]: candidate for candidate in candidates}

    installed = by_version["0.2.1"]
    assert installed["installed"] is True
    assert installed["installed_version"] == "0.2.1"
    assert installed["upgrade_available"] is False
    assert installed["repair_required"] is False
    assert installed["status"] == "installed"
    assert installed["compatible"] is True

    upgrade = by_version["0.2.2"]
    assert upgrade["installed"] is False
    assert upgrade["installed_version"] == "0.2.1"
    assert upgrade["upgrade_available"] is True
    assert upgrade["repair_required"] is False
    assert upgrade["status"] == "upgrade_available"
    assert upgrade["compatible"] is True

    missing_config = tmp_path / "missing-dsh-config"
    _write_records(store, [_record("dsh", old, missing_config)])
    broken_manager = HostManager(store, registry=FakeWinreg())
    broken = {
        candidate["version"]: candidate
        for candidate in _dsh_candidates(broken_manager)
    }["0.2.1"]
    assert broken["installed"] is True
    assert broken["installed_version"] == "0.2.1"
    assert broken["repair_required"] is True
    assert broken["upgrade_available"] is False
    assert broken["status"] == "repair_required"
    assert broken["compatible"] is True
    assert broken["config_writable"] is False


def test_unsupported_candidate_stays_incompatible(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    supported = _fake_host(tmp_path / "bin-supported" / "dsh.cmd", "dsh 0.2.1")
    unsupported = _fake_host(tmp_path / "bin-future" / "dsh.cmd", "dsh 0.3.0")
    config = _set_config(monkeypatch, "dsh", tmp_path / "dsh-config")
    monkeypatch.setenv("PATH", f"{supported.parent}{os.pathsep}{unsupported.parent}")
    store = _store(tmp_path)
    _write_records(store, [_record("dsh", supported, config)])
    manager = HostManager(store, registry=FakeWinreg())

    candidates = _dsh_candidates(manager)
    future = {candidate["version"]: candidate for candidate in candidates}["0.3.0"]

    assert future["compatible"] is False
    assert future["status"] == "version_unsupported"
    assert future["upgrade_available"] is False
    assert future["installed"] is False


def test_registry_failures_degrade_gracefully(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    executable = _fake_host(tmp_path / "bin" / "dsh.cmd", "dsh 0.2.1")
    _set_config(monkeypatch, "dsh", tmp_path / "dsh-config")
    monkeypatch.setenv("PATH", str(executable.parent))
    failing = FakeWinreg()
    failing.fail_open = True
    failing.fail_query = True
    manager = HostManager(_store(tmp_path), registry=failing)

    [candidate] = _dsh_candidates(manager)

    assert candidate["version"] == "0.2.1"
    assert candidate["status"] == "adaptable"
    assert "registry" not in candidate["sources"]

    monkeypatch.setattr(hosts_module, "winreg", None)
    without_registry = HostManager(_store(tmp_path)).discover(force=True)
    assert any(candidate.get("executable") == str(executable.resolve()) for candidate in without_registry)


def test_discovery_is_zero_write_to_host_config(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    executable = _fake_host(tmp_path / "bin" / "dsh.cmd", "dsh 0.2.1")
    config = _set_config(monkeypatch, "dsh", tmp_path / "dsh-config")
    sentinel = config / "SENTINEL.txt"
    sentinel.write_text("do not touch\n", encoding="utf-8")
    (config / "profiles" / "default").mkdir(parents=True)
    (config / "profiles" / "default" / "package.json").write_text("{}", encoding="utf-8")
    before = _snapshot(config)
    registry = FakeWinreg()
    registry.add_uninstall(
        registry.HKEY_CURRENT_USER,
        "dsh",
        {
            "DisplayName": "dsh",
            "DisplayVersion": "0.2.1",
            "InstallLocation": str(executable.parent),
        },
    )
    store = _store(tmp_path)
    _write_records(store, [_record("dsh", executable, config)])
    install_before = store.install_path.read_bytes()
    manager = HostManager(store, registry=registry)

    manager.discover(force=True)
    manager.discover(force=True)

    assert _snapshot(config) == before
    assert sentinel.read_text(encoding="utf-8") == "do not touch\n"
    assert store.install_path.read_bytes() == install_before


@pytest.mark.parametrize("kind", sorted(HOST_BINARIES))
def test_each_supported_kind_gets_stable_state_fields(tmp_path, monkeypatch, kind):
    _isolate(monkeypatch, tmp_path)
    executable = _fake_host(tmp_path / kind / f"{HOST_BINARIES[kind]}.cmd", "unrelated 1.2.3")
    _set_config(monkeypatch, kind, tmp_path / f"{kind}-config")
    monkeypatch.setenv("PATH", str(executable.parent))

    candidates = [c for c in HostManager(_store(tmp_path)).discover(force=True) if c["kind"] == kind]

    assert candidates
    for candidate in candidates:
        assert {"installed", "installed_version", "upgrade_available", "repair_required"} <= set(
            candidate
        )
        assert candidate["installed"] is False
        assert candidate["installed_version"] == ""
        assert candidate["upgrade_available"] is False
        assert candidate["repair_required"] is False
