"""Windows process invocation helpers shared by hosts and runtime adapters."""

from __future__ import annotations

import os
from pathlib import Path


def command_line(executable: str | Path, args: list[str]) -> list[str]:
    """Return argv that can launch .exe, .cmd or .bat hosts without shell=True."""
    exe = str(executable)
    if exe.lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", exe, *args]
    return [exe, *args]
