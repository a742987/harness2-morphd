"""Deterministic offline checks evaluated against the rollout workspace."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .store import sha256_file


def _safe(base: Path, rel: str) -> Path:
    rel = str(rel).replace("\\", "/")
    if rel.startswith("/") or ".." in rel.split("/"):
        raise ValueError(f"unsafe check path {rel!r}")
    path = base / rel
    root = base.resolve()
    try:
        resolved = path.resolve()
    except OSError as exc:
        raise ValueError(f"cannot resolve check path {rel!r}") from exc
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"check path escapes the workspace: {rel!r}")
    if path.is_symlink():
        raise ValueError(f"check path is a symlink: {rel!r}")
    return path


def _pointer(value, pointer: str):
    if pointer in ("", "/"):
        return value
    if not pointer.startswith("/"):
        raise ValueError(f"JSON pointer must start with '/': {pointer!r}")
    current = value
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current[token]
        else:
            raise KeyError(token)
    return current


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def evaluate_check(workspace: Path, check: dict) -> tuple[bool, str]:
    kind = check.get("type")
    try:
        if kind in ("file_exists", "file_not_exists"):
            exists = _safe(workspace, check["path"]).is_file()
            passed = exists if kind == "file_exists" else not exists
            return passed, f"{kind}: {check['path']}={exists}"
        if kind == "file_contains":
            text = _read_text(_safe(workspace, check["path"]))
            if text is None:
                return False, f"{kind}: missing {check['path']}"
            haystack = text if check.get("case_sensitive", True) else text.lower()
            needle = check["text"] if check.get("case_sensitive", True) else check["text"].lower()
            return needle in haystack, f"{kind}: {check['path']}"
        if kind == "file_regex":
            text = _read_text(_safe(workspace, check["path"]))
            if text is None:
                return False, f"{kind}: missing {check['path']}"
            pattern = str(check["pattern"])
            if len(pattern) > 1000:
                return False, "file_regex: pattern too long"
            return re.search(pattern, text) is not None, f"{kind}: {check['path']}"
        if kind == "files_glob":
            root = workspace.resolve()
            matches = [
                p
                for p in workspace.glob(str(check["glob"]))
                if p.is_file() and root in p.resolve().parents
            ]
            return len(matches) >= int(check["min_count"]), (
                f"{kind}: {check['glob']} matches={len(matches)}"
            )
        if kind == "file_sha256":
            path = _safe(workspace, check["path"])
            if not path.is_file():
                return False, f"{kind}: missing {check['path']}"
            return sha256_file(path) == check["sha256"], f"{kind}: {check['path']}"
        if kind == "json_equals":
            text = _read_text(_safe(workspace, check["path"]))
            if text is None:
                return False, f"{kind}: missing {check['path']}"
            value = json.loads(text)
            actual = _pointer(value, str(check.get("pointer", "/")))
            return actual == check.get("value"), f"{kind}: {check['path']}#{check.get('pointer', '')}"
        return False, f"unknown check type {kind!r}"
    except (OSError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
        return False, f"{kind}: {type(exc).__name__}: {exc}"


def evaluate(workspace: Path, checks: list[dict]) -> dict | None:
    if not checks:
        return None
    results = []
    n_passed = 0
    for check in checks:
        passed, detail = evaluate_check(Path(workspace), check)
        n_passed += 1 if passed else 0
        results.append({"type": check.get("type"), "passed": passed, "detail": detail})
    n = len(checks)
    return {
        "n_passed": n_passed,
        "n_criteria": n,
        "all_pass": n_passed == n,
        "checks": results,
        "frac": round(n_passed / n, 6) if n else 0.0,
    }


def delivered(workspace: Path, expected: list[str], changed: dict) -> bool:
    if expected:
        present = all(any(Path(workspace).glob(pattern)) for pattern in expected)
        return bool(present and changed)
    return bool(changed)


def matching_deliverable_paths(workspace: Path, expected: list[str]) -> list[Path]:
    out: list[Path] = []
    for pattern in expected:
        for path in sorted(Path(workspace).glob(pattern)):
            if path.is_file() and path not in out:
                out.append(path)
    return out


__all__ = [
    "delivered",
    "evaluate",
    "evaluate_check",
    "matching_deliverable_paths",
]
