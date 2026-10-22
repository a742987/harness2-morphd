"""Resolve the pinned Harness² checkout without modifying it."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDOR = REPO_ROOT / "vendor" / "harness2-src"


class UpstreamError(RuntimeError):
    pass


def _candidate_paths() -> list[Path]:
    out: list[Path] = []
    override = os.environ.get("MORPHD_HARNESS2_SRC", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
        out.append(path)
        if (path / "harness2").is_dir():
            out.append(path)
        elif (path.parent / "harness2").is_dir():
            out.append(path.parent)
    out.append(VENDOR)
    return out


def _imports() -> bool:
    try:
        importlib.import_module("harness2.core.spec")
        importlib.import_module("harness2.core.schedule")
        return True
    except Exception:
        return False


def load() -> ModuleType:
    """Import harness2, falling back to the vendored unmodified pinned checkout."""
    if _imports():
        return importlib.import_module("harness2")
    hint = (
        "Harness² is not importable. Install the pinned upstream with "
        "`pip install -e ../harness2` (commit 1cfe254e8d25) or set "
        "MORPHD_HARNESS2_SRC to its checkout. harness2-morphd never patches upstream."
    )
    for path in _candidate_paths():
        if (path / "harness2" / "__init__.py").is_file():
            for name in [n for n in sys.modules if n == "harness2" or n.startswith("harness2.")]:
                del sys.modules[name]
            sys.path.insert(0, str(path))
            if _imports():
                return importlib.import_module("harness2")
            sys.path.remove(str(path))
    raise UpstreamError(hint)


def module(name: str) -> ModuleType:
    load()
    return importlib.import_module(name)


def core(name: str) -> ModuleType:
    return module(f"harness2.core.{name}")


def substrate(name: str) -> ModuleType:
    return module(f"harness2.substrate.{name}")
