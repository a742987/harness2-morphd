"""Versioned, backup-first migrations for the local consumer data directory.

The runner is deliberately small: every JSON target is read, upgraded in memory, and only
rewritten when its top-level ``version`` is older than :data:`CURRENT_VERSION`. Before the
first write all originals are copied to ``home/backups/migrations/<timestamp>/<relative>``.
If a write fails, every target is restored from that backup. Corrupt or future-version files
are never modified; they are only reported (and the store logs a redacted warning).

Only versioned metadata files and record collections are touched. Exports, spool files,
runtime state and session workspaces are intentionally excluded: they are either not
versioned records or must stay byte-identical for other consumers.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import store as engine_store

CURRENT_VERSION = 1

DATA_FILES = ("config.json", "providers.json", "install.json", "state.json")
RECORD_COLLECTIONS = ("projects", "sessions", "jobs", "candidates", "snapshots")


def _migrate_v0_to_v1(payload: dict) -> dict:
    """v0 records predate the top-level version field; no other shape changed."""
    return {**payload, "version": 1}


MIGRATIONS: dict[int, Callable[[dict], dict]] = {0: _migrate_v0_to_v1}


@dataclass
class MigrationReport:
    """Outcome of one migration pass. Paths are home-relative POSIX strings."""

    current_version: int = CURRENT_VERSION
    imported: list[str] = field(default_factory=list)
    migrated: list[str] = field(default_factory=list)
    backed_up: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    backup_dir: str | None = None
    rolled_back: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.migrated)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.rolled_back

    def to_dict(self) -> dict:
        return {
            "current_version": self.current_version,
            "imported": list(self.imported),
            "migrated": list(self.migrated),
            "backed_up": list(self.backed_up),
            "skipped": list(self.skipped),
            "errors": [dict(item) for item in self.errors],
            "warnings": [dict(item) for item in self.warnings],
            "backup_dir": self.backup_dir,
            "rolled_back": self.rolled_back,
        }


@dataclass
class _PlannedMigration:
    path: Path
    relative: str
    payload: dict
    mode: int


def target_paths(home: Path) -> list[Path]:
    """List existing files covered by migrations, in deterministic order."""
    home = Path(home)
    paths = [home / name for name in DATA_FILES if (home / name).is_file()]
    for collection in RECORD_COLLECTIONS:
        root = home / collection
        if not root.is_dir():
            continue
        paths.extend(sorted(root.glob("*.json"), key=lambda item: item.name))
    return [path for path in paths if path.is_file()]


def run_migrations(home: Path, *, current_version: int = CURRENT_VERSION) -> MigrationReport:
    """Migrate ``home`` in place and return a report instead of raising on data errors."""
    home = Path(home)
    report = MigrationReport(current_version=current_version)
    plan: list[_PlannedMigration] = []

    for path in target_paths(home):
        relative = path.relative_to(home).as_posix()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            report.skipped.append(relative)
            report.errors.append(
                {"path": relative, "reason": "read_failed", "error": type(exc).__name__}
            )
            continue
        except ValueError:
            report.skipped.append(relative)
            report.errors.append({"path": relative, "reason": "invalid_json"})
            continue
        if not isinstance(payload, dict):
            report.skipped.append(relative)
            report.errors.append({"path": relative, "reason": "not_an_object"})
            continue

        report.imported.append(relative)
        version = payload.get("version", 0)
        if isinstance(version, bool) or not isinstance(version, int):
            report.skipped.append(relative)
            report.warnings.append({"path": relative, "reason": "invalid_version"})
            continue
        if version > current_version:
            report.skipped.append(relative)
            report.warnings.append(
                {"path": relative, "reason": "future_version", "version": version}
            )
            continue
        if version == current_version:
            report.skipped.append(relative)
            continue

        upgraded = payload
        while version < current_version:
            step = MIGRATIONS.get(version)
            if step is None:
                upgraded = None
                report.skipped.append(relative)
                report.warnings.append(
                    {"path": relative, "reason": "no_migration_path", "version": version}
                )
                break
            upgraded = step(upgraded)
            next_version = upgraded.get("version")
            if (
                isinstance(next_version, bool)
                or not isinstance(next_version, int)
                or next_version <= version
            ):
                upgraded = None
                report.skipped.append(relative)
                report.errors.append({"path": relative, "reason": "invalid_migration"})
                break
            version = next_version
        if upgraded is None:
            continue
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            mode = 0o600
        plan.append(_PlannedMigration(path, relative, upgraded, mode))

    if not plan:
        return report

    backup_root = _create_backup_dir(home)
    if backup_root is None:
        report.errors.append({"path": ".", "reason": "backup_failed"})
        return report
    report.backup_dir = str(backup_root)
    for entry in plan:
        destination = backup_root / entry.relative
        try:
            _ensure_private_dir(destination.parent)
            engine_store.atomic_write_bytes(destination, entry.path.read_bytes())
            _chmod_private(destination, 0o600)
        except OSError as exc:
            report.errors.append(
                {"path": entry.relative, "reason": "backup_failed", "error": type(exc).__name__}
            )
            shutil.rmtree(backup_root, ignore_errors=True)
            report.backup_dir = None
            report.backed_up.clear()
            return report
        report.backed_up.append(entry.relative)

    for entry in plan:
        try:
            engine_store.atomic_write_json(entry.path, entry.payload)
            _chmod_private(entry.path, _private_mode(entry.mode))
        except Exception as exc:  # roll back on any write/serialisation failure
            report.errors.append(
                {"path": entry.relative, "reason": "write_failed", "error": type(exc).__name__}
            )
            _restore_backups(backup_root, plan, report)
            return report
        report.migrated.append(entry.relative)
    return report


def _create_backup_dir(home: Path) -> Path | None:
    stamp = engine_store.utc_stamp()
    root = home / "backups" / "migrations"
    candidate = root / stamp
    suffix = 1
    while True:
        try:
            _ensure_private_dir(candidate, exist_ok=False)
            return candidate
        except FileExistsError:
            candidate = root / f"{stamp}-{suffix}"
            suffix += 1
        except OSError:
            return None


def _restore_backups(
    backup_root: Path, plan: list[_PlannedMigration], report: MigrationReport
) -> None:
    for entry in plan:
        backup = backup_root / entry.relative
        try:
            _chmod_private(entry.path, 0o600)
            engine_store.atomic_write_bytes(entry.path, backup.read_bytes())
            _chmod_private(entry.path, entry.mode)
        except OSError as exc:
            report.errors.append(
                {"path": entry.relative, "reason": "restore_failed", "error": type(exc).__name__}
            )
    report.migrated.clear()
    report.rolled_back = True


def _private_mode(mode: int) -> int:
    private = stat.S_IMODE(mode) & 0o600
    return private or 0o600


def _ensure_private_dir(path: Path, *, exist_ok: bool = True) -> None:
    path.mkdir(parents=True, exist_ok=exist_ok)
    _chmod_private(path, 0o700)


def _chmod_private(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass
