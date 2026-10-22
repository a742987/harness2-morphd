"""Small shared helpers for the consumer layer."""

from __future__ import annotations

import difflib
import fnmatch
import hashlib
import os
import shutil
import time
import uuid
from pathlib import Path

from .. import sanitize
from .. import store as engine_store
from .errors import ConsumerError

TEXT_LIMIT = 1_000_000
MAX_CONTEXT_FILES = 20_000
MAX_CONTEXT_BYTES = 250 * 1024 * 1024  # 250MB


def new_id(prefix: str) -> str:
    return f"{prefix}_{time.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:16]}"


def safe_rel(rel: str) -> str:
    value = str(rel).replace("\\", "/").strip()
    if not value or value.startswith("/") or "\x00" in value:
        raise ConsumerError("CANDIDATE_UNSAFE", "文件路径不安全", "检查候选文件路径。")
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ConsumerError("CANDIDATE_UNSAFE", "文件路径包含 ..", "检查候选文件路径。")
    return "/".join(parts)


def excluded(rel: str, globs: list[str]) -> bool:
    rel = rel.replace("\\", "/")
    for pattern in globs or []:
        pattern = pattern.replace("\\", "/")
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch("/" + rel, pattern):
            return True
        if pattern.endswith("/**") and rel.startswith(pattern[:-3].rstrip("/") + "/"):
            return True
        if fnmatch.fnmatch(Path(rel).name, pattern):
            return True
    return False


def is_secret_path(rel: str) -> bool:
    name = Path(rel).name
    if sanitize.is_secret_filename(name):
        return True
    parts = set(Path(rel).parts)
    return bool(parts & {".ssh", ".aws", ".gnupg", ".kube", ".docker", ".password-store"})


def is_binary(data: bytes) -> bool:
    if b"\x00" in data[:4096]:
        return True
    sample = data[:4096]
    if not sample:
        return False
    printable = sum(byte in b"\n\r\t\b\f" or 32 <= byte < 127 for byte in sample)
    return printable / len(sample) < 0.85


def copy_context(
    src: Path,
    dst: Path,
    *,
    globs: list[str] | None = None,
    extra_excludes: set[str] | None = None,
    max_files: int = MAX_CONTEXT_FILES,
    max_bytes: int = MAX_CONTEXT_BYTES,
) -> dict:
    """Copy a sanitised, filtered workspace copy for a session task/candidate context."""
    src = Path(src).resolve()
    dst = Path(dst)
    manifest = {"files": 0, "bytes": 0, "skipped": [], "redacted": [], "truncated": False}
    if not src.is_dir():
        return manifest
    dst.mkdir(parents=True, exist_ok=True)
    excludes = extra_excludes or set()
    for path in engine_store.iter_files(src):
        if manifest["files"] >= max_files:
            manifest["truncated"] = True
            break
        rel = path.relative_to(src).as_posix()
        if rel in excludes or excluded(rel, globs or []) or is_secret_path(rel):
            manifest["skipped"].append(rel)
            continue
        try:
            if path.stat().st_size > TEXT_LIMIT:
                manifest["skipped"].append(rel)
                continue
            data = path.read_bytes()
        except OSError:
            manifest["skipped"].append(rel)
            continue
        if manifest["bytes"] + len(data) > max_bytes:
            manifest["truncated"] = True
            break
        if is_binary(data):
            manifest["skipped"].append(rel)
            continue
        text = data.decode("utf-8", errors="replace")
        cleaned, labels = sanitize.redact_text(text)
        if labels:
            manifest["redacted"].append({"path": rel, "labels": labels})
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        engine_store.atomic_write_text(out, cleaned)
        try:
            shutil.copymode(path, out)
        except OSError:
            pass
        manifest["files"] += 1
        manifest["bytes"] += len(data)
    return manifest


def read_tree(root: Path, *, limit_bytes: int = 256 * 1024) -> dict[str, str]:
    tree: dict[str, str] = {}
    total = 0
    root = Path(root)
    for path in engine_store.iter_files(root):
        rel = path.relative_to(root).as_posix()
        if is_secret_path(rel):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if is_binary(data):
            continue
        if len(data) > limit_bytes:
            tree[rel] = data[:limit_bytes].decode("utf-8", errors="replace") + "\n…(truncated)"
            continue
        if total + len(data) > 2 * 1024 * 1024:
            continue
        total += len(data)
        tree[rel] = data.decode("utf-8", errors="replace")
    return tree


def tree_hashes(root: Path) -> dict[str, str]:
    root = Path(root)
    hashes: dict[str, str] = {}
    for path in engine_store.iter_files(root):
        try:
            hashes[path.relative_to(root).as_posix()] = engine_store.sha256_file(path)
        except OSError:
            continue
    return hashes


def diff_trees(base: dict[str, str], candidate: dict[str, str]) -> tuple[str, list[str], int]:
    changed: list[str] = []
    lines_added = 0
    lines_removed = 0
    chunks: list[str] = []
    for rel in sorted(set(base) | set(candidate)):
        before = base.get(rel)
        after = candidate.get(rel)
        if before == after:
            continue
        changed.append(rel)
        if before is None:
            before = ""
            old_name = "/dev/null"
        else:
            old_name = f"a/{rel}"
        if after is None:
            after = ""
            new_name = "/dev/null"
        else:
            new_name = f"b/{rel}"
        diff = list(
            difflib.unified_diff(
                before.splitlines(),
                after.splitlines(),
                fromfile=old_name,
                tofile=new_name,
                lineterm="",
            )
        )
        for line in diff:
            if line.startswith("+") and not line.startswith("+++"):
                lines_added += 1
            elif line.startswith("-") and not line.startswith("---"):
                lines_removed += 1
        chunks.extend([f"diff --git a/{rel} b/{rel}", *diff, ""])
    return "\n".join(chunks), changed, lines_added + lines_removed


def tree_summary(tree: dict[str, str]) -> str:
    names = sorted(tree)
    if not names:
        return "(no harness files)"
    return "\n".join(f"- {name} ({len(tree[name])} bytes)" for name in names)


def estimate_tokens(text: str) -> int:
    # Conservative local estimate; providers report actual usage after the call.
    return max(1, (len(text) + 3) // 4)


def chmod_600(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
