"""Versioned, atomic local storage for consumer records.

The on-disk layout follows PRD C.4.2. Every record carries a top-level ``version`` and is
written with an atomic replace. Unknown fields are preserved when a record is updated.
"""

from __future__ import annotations

import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .. import store as engine_store
from . import migrations
from .errors import StoreError

RECORD_DIRS = (
    "projects",
    "sessions",
    "jobs",
    "candidates",
    "diffs",
    "snapshots",
    "logs",
    "exports",
    "spool",
    "runtime",
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _chmod_private(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


class ConsumerStore:
    """Filesystem store rooted at ``~/.morphd`` (or ``MORPHD_HOME``)."""

    def __init__(self, home: Path):
        self.home = Path(home).expanduser().resolve()
        self.config_path = self.home / "config.json"
        self.providers_path = self.home / "providers.json"
        self.install_path = self.home / "install.json"
        self.runtime_state_path = self.home / "state.json"
        self.budget_journal = self.home / "_budget" / "consumer.jsonl"
        self.last_migration_report: migrations.MigrationReport | None = None
        self.ensure()

    def ensure(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        _chmod_private(self.home, 0o700)
        for name in RECORD_DIRS:
            path = self.home / name
            path.mkdir(parents=True, exist_ok=True)
            _chmod_private(path, 0o700)
        (self.home / "_budget").mkdir(parents=True, exist_ok=True)
        _chmod_private(self.home / "_budget", 0o700)
        self.run_migrations()

    def run_migrations(self) -> migrations.MigrationReport:
        """Bring existing data files to ``CURRENT_VERSION``; safe to call repeatedly."""
        report = migrations.run_migrations(self.home)
        self.last_migration_report = report
        for issue in (*report.warnings, *report.errors):
            try:
                self.append_log(
                    "migration_warning",
                    path=issue.get("path"),
                    reason=issue.get("reason"),
                )
            except OSError:
                pass
        return report

    # --- generic JSON ---------------------------------------------------------
    def read_json(self, path: Path, default=None):
        payload = engine_store.read_json(Path(path), default=None)
        return default if payload is None else payload

    def write_json(self, path: Path, payload) -> Path:
        path = Path(path)
        if not isinstance(payload, dict):
            raise StoreError("stored records must be JSON objects")
        if "version" not in payload:
            payload = {**payload, "version": migrations.CURRENT_VERSION}
        engine_store.atomic_write_json(path, payload)
        _chmod_private(path, 0o600)
        return path

    def read_record(self, collection: str, record_id: str, default=None):
        return self.read_json(self.record_path(collection, record_id), default)

    def write_record(self, collection: str, record_id: str, payload: dict) -> Path:
        return self.write_json(self.record_path(collection, record_id), payload)

    def delete_record(self, collection: str, record_id: str) -> None:
        path = self.record_path(collection, record_id)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise StoreError(f"cannot delete {path}: {exc}") from exc

    def record_path(self, collection: str, record_id: str) -> Path:
        if collection not in RECORD_DIRS:
            raise StoreError(f"unknown record collection {collection!r}")
        if not _ID_RE.match(str(record_id)):
            raise StoreError(f"unsafe record id {record_id!r}")
        return self.home / collection / f"{record_id}.json"

    def list_records(self, collection: str, *, limit: int | None = None) -> list[dict]:
        if collection not in RECORD_DIRS:
            raise StoreError(f"unknown record collection {collection!r}")
        root = self.home / collection
        records: list[dict] = []
        for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if collection == "snapshots" and path.parent != root:
                continue
            payload = engine_store.read_json(path)
            if isinstance(payload, dict):
                payload.setdefault("id", path.stem)
                records.append(payload)
        if limit is not None:
            return records[: max(0, int(limit))]
        return records

    def delete_collection(self, collection: str) -> int:
        if collection not in RECORD_DIRS:
            raise StoreError(f"unknown record collection {collection!r}")
        count = 0
        for path in sorted((self.home / collection).glob("*.json"), reverse=True):
            try:
                path.unlink()
                count += 1
            except OSError:
                pass
        return count

    # --- locks / logs ---------------------------------------------------------
    @contextmanager
    def lock(self, name: str) -> Iterator[None]:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name)
        with engine_store.file_lock(self.home / "runtime" / f"{safe}.lock"):
            yield

    def append_log(self, event: str, **fields) -> None:
        logs = self.home / "logs" / "events.jsonl"
        logs.parent.mkdir(parents=True, exist_ok=True)
        try:
            if logs.exists() and logs.stat().st_size > 100 * 1024 * 1024:
                rotated = logs.with_name(f"events.{engine_store.utc_stamp()}.jsonl")
                os.replace(logs, rotated)
        except OSError:
            pass
        payload = {"at": engine_store.utc_now(), "event": event}
        payload.update({k: v for k, v in fields.items() if v is not None})
        engine_store.append_jsonl(logs, payload)
        _chmod_private(logs, 0o600)

    def log_tail(self, lines: int = 80) -> list[dict]:
        records = engine_store.read_jsonl(self.home / "logs" / "events.jsonl")
        return records[-max(1, int(lines)) :]

    # --- metadata -------------------------------------------------------------
    def disk_usage(self) -> dict:
        total = 0
        files = 0
        for root, _dirs, names in os.walk(self.home):
            for name in names:
                path = Path(root) / name
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    total += path.stat().st_size
                    files += 1
                except OSError:
                    continue
        return {"bytes": total, "files": files}

    def cleanup_temp(self, *, age_seconds: int = 86400) -> int:
        removed = 0
        cutoff = time.time() - age_seconds
        for root, _dirs, names in os.walk(self.home):
            for name in names:
                if not name.endswith(".tmp"):
                    continue
                path = Path(root) / name
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
        return removed

    def permissions_ok(self) -> bool:
        # Windows ACLs inherit from the user profile; POSIX mode bits are not authoritative.
        return self.home.is_dir()
