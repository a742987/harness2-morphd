from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from morphd import store as engine_store
from morphd.consumer.migrations import CURRENT_VERSION
from morphd.consumer.store import RECORD_DIRS, ConsumerStore


def _write_json(path: Path, payload: dict, *, indent: int | None = 2) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=indent, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return path.read_bytes()


def _backup_dirs(home: Path) -> list[Path]:
    root = home / "backups" / "migrations"
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir())


def test_v0_migrates_and_preserves_unknown_fields_and_values(tmp_path):
    home = tmp_path / "home"
    payloads = {
        "config.json": {
            "enabled": True,
            "trust_level": "advisor",
            "future_setting": {"nested": [1, 2, {"keep": "yes"}]},
        },
        "providers.json": {
            "providers": [
                {
                    "id": "p_01",
                    "label": "Fake",
                    "key_ref": "keychain:morphd:p_01",
                    "unknown_provider_field": 7,
                }
            ]
        },
        "install.json": {"app_version": "0.5.0", "hosts": [{"host": "dsh"}]},
        "state.json": {"version": 0, "paused_until": None, "weekly": {"count": 3}},
    }
    for name, payload in payloads.items():
        _write_json(home / name, payload)

    store = ConsumerStore(home)
    report = store.last_migration_report

    assert report is not None
    assert report.changed is True
    assert set(report.migrated) == set(payloads)
    assert report.backed_up == report.migrated
    assert set(report.imported) == set(payloads)
    for name, original in payloads.items():
        migrated = json.loads((home / name).read_text(encoding="utf-8"))
        assert migrated["version"] == CURRENT_VERSION
        assert {key: value for key, value in migrated.items() if key != "version"} == {
            key: value for key, value in original.items() if key != "version"
        }

    assert store.read_json(store.providers_path)["providers"][0]["key_ref"] == (
        "keychain:morphd:p_01"
    )
    assert store.last_migration_report is report


def test_second_run_is_noop_and_keeps_bytes_and_single_backup(tmp_path):
    home = tmp_path / "home"
    _write_json(home / "config.json", {"enabled": True, "custom": {"a": 1}})
    _write_json(home / "projects" / "proj_01.json", {"id": "proj_01", "root": "/tmp/project"})
    paths = [home / "config.json", home / "projects" / "proj_01.json"]
    originals = {path: path.read_bytes() for path in paths}

    store = ConsumerStore(home)
    migrated = {path: path.read_bytes() for path in paths}
    assert len(_backup_dirs(home)) == 1

    report = store.run_migrations()
    assert report.migrated == []
    assert report.backed_up == []
    assert len(_backup_dirs(home)) == 1
    assert {path: path.read_bytes() for path in paths} == migrated

    second = ConsumerStore(home)
    assert second.last_migration_report is not None
    assert second.last_migration_report.migrated == []
    assert second.last_migration_report.backed_up == []
    assert len(_backup_dirs(home)) == 1
    assert {path: path.read_bytes() for path in paths} == migrated
    assert all(original != final for original, final in zip(originals.values(), migrated.values()))


def test_current_version_file_is_never_rewritten(tmp_path):
    home = tmp_path / "home"
    path = home / "config.json"
    path.parent.mkdir(parents=True)
    raw = b'{ "z": 2, "version": 1, "a": 1 }\n'
    path.write_bytes(raw)

    store = ConsumerStore(home)

    assert path.read_bytes() == raw
    assert store.last_migration_report is not None
    assert store.last_migration_report.migrated == []
    assert store.last_migration_report.skipped == ["config.json"]
    assert _backup_dirs(home) == []


def test_backup_created_once_and_contains_original_bytes(tmp_path):
    home = tmp_path / "home"
    payloads = {
        "config.json": {"enabled": False, "unknown": [1, 2, 3]},
        "providers.json": {"providers": [{"id": "p_01", "key_ref": "keychain:morphd:p_01"}]},
        "projects/proj_01.json": {"id": "proj_01", "root": "/tmp/project"},
    }
    originals = {
        str(name): _write_json(home / name, payload) for name, payload in payloads.items()
    }

    store = ConsumerStore(home)
    backup_dirs = _backup_dirs(home)
    assert len(backup_dirs) == 1
    assert store.last_migration_report is not None
    assert store.last_migration_report.backup_dir == str(backup_dirs[0])
    for name, raw in originals.items():
        assert (backup_dirs[0] / name).read_bytes() == raw
    assert set(store.last_migration_report.backed_up) == set(originals)

    store.run_migrations()
    assert len(_backup_dirs(home)) == 1


def test_corrupt_json_untouched_and_warning_redacted(tmp_path):
    home = tmp_path / "home"
    corrupt = home / "config.json"
    corrupt.parent.mkdir(parents=True)
    corrupt_raw = b'{"version": 1, "api_key": "sk-should-never-be-logged",'
    corrupt.write_bytes(corrupt_raw)
    _write_json(
        home / "providers.json",
        {"version": 1, "providers": [{"id": "p_01", "key_ref": "keychain:morphd:p_01"}]},
    )
    _write_json(home / "projects" / "proj_01.json", {"id": "proj_01"})

    store = ConsumerStore(home)
    report = store.last_migration_report

    assert corrupt.read_bytes() == corrupt_raw
    assert report is not None
    assert any(
        error["path"] == "config.json" and error["reason"] == "invalid_json"
        for error in report.errors
    )
    assert report.rolled_back is False
    assert set(report.migrated) == {"projects/proj_01.json"}
    assert "config.json" not in report.backed_up
    backup_dirs = _backup_dirs(home)
    assert len(backup_dirs) == 1
    assert not (backup_dirs[0] / "config.json").exists()

    events = json.dumps(store.log_tail(), ensure_ascii=False)
    assert "migration_warning" in events
    assert "invalid_json" in events
    assert "sk-should-never-be-logged" not in events


def test_failed_write_rolls_back_every_changed_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    payloads = {
        "config.json": {"enabled": True, "custom": {"a": 1}},
        "providers.json": {"providers": [{"id": "p_01", "key_ref": "keychain:morphd:p_01"}]},
        "projects/proj_01.json": {"id": "proj_01", "root": "/tmp/project"},
    }
    originals = {
        str(name): _write_json(home / name, payload) for name, payload in payloads.items()
    }

    real_write = engine_store.atomic_write_json
    calls = 0

    def flaky_write(path, payload):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disk full")
        return real_write(path, payload)

    monkeypatch.setattr(engine_store, "atomic_write_json", flaky_write)
    store = ConsumerStore(home)
    report = store.last_migration_report

    assert calls == 2
    assert report is not None
    assert report.rolled_back is True
    assert report.migrated == []
    assert any(error["reason"] == "write_failed" for error in report.errors)
    for name, raw in originals.items():
        assert (home / name).read_bytes() == raw
        migrated = json.loads(raw)
        assert "version" not in migrated

    backup_dirs = _backup_dirs(home)
    assert len(backup_dirs) == 1
    for name, raw in originals.items():
        assert (backup_dirs[0] / name).read_bytes() == raw


def test_future_version_untouched_and_warned(tmp_path):
    home = tmp_path / "home"
    path = home / "providers.json"
    payload = {"version": CURRENT_VERSION + 1, "providers": [], "unknown": {"keep": True}}
    raw = _write_json(path, payload)

    store = ConsumerStore(home)
    report = store.last_migration_report

    assert path.read_bytes() == raw
    assert report is not None
    assert report.migrated == []
    assert report.skipped == ["providers.json"]
    assert any(
        warning["path"] == "providers.json"
        and warning["reason"] == "future_version"
        and warning["version"] == CURRENT_VERSION + 1
        for warning in report.warnings
    )
    assert _backup_dirs(home) == []

    ConsumerStore(home)
    assert path.read_bytes() == raw
    assert _backup_dirs(home) == []


def test_collection_records_migrate(tmp_path):
    home = tmp_path / "home"
    expected: dict[str, dict] = {}
    for index, collection in enumerate(("projects", "sessions", "jobs", "candidates", "snapshots")):
        record = {"id": f"{collection}_01", "payload": {"index": index, "keep": [1, 2]}}
        _write_json(home / collection / f"{collection}_01.json", record)
        expected[collection] = record
    nested = home / "sessions" / "sessions_01" / "workspace" / "notes.json"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b'{"not": "a record"}')

    store = ConsumerStore(home)
    report = store.last_migration_report

    assert report is not None
    assert set(report.migrated) == {
        f"{collection}/{collection}_01.json" for collection in expected
    }
    for collection, record in expected.items():
        migrated = store.read_record(collection, f"{collection}_01")
        assert migrated["version"] == CURRENT_VERSION
        assert migrated["id"] == record["id"]
        assert migrated["payload"] == record["payload"]
    assert nested.read_bytes() == b'{"not": "a record"}'


def test_migrated_record_permissions_are_not_widened(tmp_path):
    home = tmp_path / "home"
    path = home / "projects" / "proj_01.json"
    _write_json(path, {"id": "proj_01", "root": "/tmp/project"})
    os.chmod(path, 0o600)
    before = stat.S_IMODE(path.stat().st_mode)

    ConsumerStore(home)

    after = stat.S_IMODE(path.stat().st_mode)
    assert after & ~before == 0
    if os.name != "nt":
        assert after == 0o600


def test_fresh_empty_home_starts_without_migration_writes(tmp_path):
    home = tmp_path / "home"

    store = ConsumerStore(home)

    assert home.is_dir()
    for name in RECORD_DIRS:
        assert (home / name).is_dir()
    assert store.last_migration_report is not None
    assert store.last_migration_report.to_dict()["migrated"] == []
    assert store.last_migration_report.errors == []
    assert _backup_dirs(home) == []
