"""Small filesystem primitives shared by the pipeline."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None  # type: ignore[assignment]

WORKSPACE_IGNORES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".morphd",
}
_IGNORED_SUFFIXES = {".pyc", ".pyo", ".swp", ".tmp"}


class StoreError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def atomic_write_bytes(path: Path, data: bytes) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    return atomic_write_bytes(path, text.encode(encoding))


def atomic_write_json(path: Path, payload) -> Path:
    return atomic_write_text(
        path, json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    )


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def append_jsonl(path: Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8", errors="replace"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_hash(payload) -> str:
    return sha256_text(canonical_json(payload))


def iter_files(root: Path, *, ignores: Iterable[str] = WORKSPACE_IGNORES) -> Iterator[Path]:
    root = Path(root)
    ignore = set(ignores)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in ignore and not d.startswith(".git"))
        for name in sorted(filenames):
            if name.endswith(tuple(_IGNORED_SUFFIXES)):
                continue
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            yield path


def workspace_manifest(root: Path, *, ignores: Iterable[str] = WORKSPACE_IGNORES) -> dict:
    root = Path(root)
    files: dict[str, dict] = {}
    digest = hashlib.sha256()
    for path in iter_files(root, ignores=ignores):
        rel = path.relative_to(root).as_posix()
        try:
            st = path.stat()
        except OSError:
            continue
        file_hash = sha256_file(path)
        executable = bool(st.st_mode & stat.S_IXUSR)
        files[rel] = {"sha256": file_hash, "size": st.st_size, "executable": executable}
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(b"1" if executable else b"0")
        digest.update(b"\0")
        digest.update(file_hash.encode())
        digest.update(b"\0")
    return {"sha256": digest.hexdigest(), "files": files, "n_files": len(files)}


def tree_hash(root: Path, *, ignores: Iterable[str] = WORKSPACE_IGNORES) -> str:
    return workspace_manifest(root, ignores=ignores)["sha256"]


def safe_slug(value: str, *, max_len: int = 80) -> str:
    raw = str(value).replace("\\", "/").replace("/", "__")
    raw = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-._")
    if not raw:
        raw = "run"
    if len(raw) <= max_len:
        return raw
    return f"{raw[: max_len - 17]}-{sha256_text(value)[:16]}"


def ensure_within(base: Path, rel: str) -> Path:
    rel = str(rel).replace("\\", "/")
    if rel.startswith("/") or ".." in rel.split("/") or "\x00" in rel:
        raise StoreError(f"unsafe relative path: {rel!r}")
    return Path(base) / rel


def relative_path(root: Path, path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError as exc:
        raise StoreError(f"{path} is outside {root}") from exc


@contextlib.contextmanager
def file_lock(path: Path, *, timeout: float = 30.0) -> Iterator[None]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+")
    deadline = time.monotonic() + timeout
    try:
        locked = False
        while True:
            try:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                elif msvcrt is not None:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    raise StoreError("no file-locking primitive is available on this platform")
                locked = True
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise StoreError(f"timed out waiting for lock {path}") from None
                time.sleep(0.05)
        yield
    finally:
        try:
            if locked:
                if fcntl is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                elif msvcrt is not None:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            fh.close()


def copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    data = Path(src).read_bytes()
    atomic_write_bytes(dst, data)
    mode = Path(src).stat().st_mode
    os.chmod(dst, stat.S_IMODE(mode) & 0o755)


def read_text_limited(path: Path, limit: int = 512_000) -> str:
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return ""
    if st.st_size > limit:
        with open(path, "rb") as fh:
            return fh.read(limit).decode("utf-8", errors="replace") + "\n…(truncated)"
    return path.read_text(encoding="utf-8", errors="replace")
