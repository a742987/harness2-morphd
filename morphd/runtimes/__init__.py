"""Runtime adapters. Import lazily so `morphd --help` needs no upstream checkout."""

from __future__ import annotations

from importlib import import_module

from ..config import RUNTIMES

_MODULES = {
    "dsh": "morphd.runtimes.dsh",
    "codex": "morphd.runtimes.codex",
    "claude-code": "morphd.runtimes.claudecode",
}


def load(runtime: str):
    if runtime not in RUNTIMES:
        raise ValueError(f"unknown runtime {runtime!r}; expected {RUNTIMES}")
    return import_module(_MODULES[runtime])


def build_adapter(runtime: str, **kwargs):
    module = load(runtime)
    return module.build_adapter(**kwargs)


__all__ = ["build_adapter", "load"]
