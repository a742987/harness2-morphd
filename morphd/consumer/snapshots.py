"""Pre-apply snapshots of the harness files harness2-morphd is allowed to edit."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .. import store as engine_store
from .errors import ConsumerError
from .hosts import HostManager
from .settings import ProjectManager, SettingsManager
from .store import ConsumerStore
from .util import new_id, tree_hashes


def tree_digest(tree: dict[str, str]) -> str:
    return engine_store.payload_hash({rel: engine_store.sha256_text(text) for rel, text in sorted(tree.items())})


class SnapshotManager:
    def __init__(self, store: ConsumerStore):
        self.store = store
        self.hosts = HostManager(store)

    def create(
        self,
        *,
        kind: str,
        project_root: str,
        reason: str,
        candidate_id: str | None = None,
    ) -> dict:
        tree = self.hosts.read_harness_tree(kind, project_root=project_root)
        project = ProjectManager(self.store).for_root(Path(project_root))
        snapshot_id = new_id("snap")
        backup_root = self.store.home / "snapshots" / snapshot_id
        backup_root.mkdir(parents=True, exist_ok=True)
        entries = []
        for rel, text in sorted(tree.items()):
            target = self.hosts.target_for(kind, rel, project_root=project_root)
            data = text.encode("utf-8")
            backup = backup_root / "files" / rel
            engine_store.atomic_write_bytes(backup, data)
            entries.append(
                {
                    "path": str(target),
                    "rel": rel,
                    "kind": "file",
                    "backup_path": str(backup),
                    "sha256": engine_store.sha256_text(text),
                }
            )
        manifest = {
            "version": 1,
            "id": snapshot_id,
            "host": kind,
            "project_id": project["id"],
            "project": str(Path(project_root).resolve()),
            "created_at": engine_store.utc_now(),
            "reason": reason,
            "candidate_id": candidate_id,
            "entries": entries,
            "base_hash": tree_digest(tree),
            "hashes": tree_hashes(Path(backup_root / "files")),
        }
        self.store.write_record("snapshots", snapshot_id, manifest)
        keep = int(
            SettingsManager(self.store)
            .load()
            .get("privacy", {})
            .get("snapshot_limit", 10)
            or 10
        )
        self._prune(project["id"], keep=max(5, keep))
        self.store.append_log(
            "snapshot_created", snapshot_id=snapshot_id, host=kind, reason=reason, files=len(entries)
        )
        return manifest

    def _prune(self, project_id: str, *, keep: int = 10) -> None:
        manifests = [
            item
            for item in self.store.list_records("snapshots")
            if item.get("project_id") == project_id
        ]
        if len(manifests) <= keep:
            return
        # Keep the newest N and always pin the project's earliest snapshot so that
        # "restore original harness" remains available after many applications.
        pinned = sorted(manifests, key=lambda item: item.get("created_at", ""))[0].get("id")
        prunable = [
            item
            for item in manifests[keep:]
            if item.get("id") != pinned
        ]
        for item in prunable:
            sid = str(item.get("id"))
            self.store.delete_record("snapshots", sid)
            shutil.rmtree(self.store.home / "snapshots" / sid, ignore_errors=True)

    def get(self, snapshot_id: str) -> dict:
        manifest = self.store.read_record("snapshots", snapshot_id)
        if not isinstance(manifest, dict):
            raise ConsumerError("NOT_FOUND", "快照不存在或已过期。", "刷新历史页。")
        return manifest

    def restore(self, manifest: dict, *, expected_hashes: dict[str, str] | None = None) -> list[str]:
        restored = []
        for entry in manifest.get("entries", []):
            target = Path(str(entry.get("path")))
            backup = Path(str(entry.get("backup_path") or ""))
            if expected_hashes is not None:
                rel = str(entry.get("rel"))
                current = expected_hashes.get(rel)
                if current and current != entry.get("sha256"):
                    # User edited the file after apply: do not silently overwrite.
                    raise ConsumerError(
                        "APPLY_CONFLICT",
                        f"{rel} 已被手动修改，撤销会覆盖你的改动。",
                        "先备份/提交该文件，或手动恢复。",
                    )
            if not backup.is_file():
                raise ConsumerError("NOT_FOUND", "快照文件缺失，无法撤销。", "使用原始 harness 恢复或手动处理。")
            engine_store.atomic_write_bytes(target, backup.read_bytes())
            try:
                os.chmod(target, 0o644)
            except OSError:
                pass
            restored.append(str(entry.get("rel")))
        return restored

    def first_for_project(self, project_id: str) -> dict | None:
        manifests = [
            item
            for item in self.store.list_records("snapshots")
            if item.get("project_id") == project_id and item.get("reason") in ("original", "first-apply")
        ]
        if manifests:
            return sorted(manifests, key=lambda item: item.get("created_at", ""))[0]
        all_for_project = [
            item
            for item in self.store.list_records("snapshots")
            if item.get("project_id") == project_id
        ]
        if not all_for_project:
            return None
        return sorted(all_for_project, key=lambda item: item.get("created_at", ""))[0]
