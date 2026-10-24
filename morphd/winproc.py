"""Windows process invocation helpers shared by hosts and runtime adapters."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def command_line(executable: str | Path, args: list[str]) -> list[str]:
    """Return argv that can launch .exe, .cmd or .bat hosts without shell=True."""
    exe = str(executable)
    if exe.lower().endswith((".cmd", ".bat")):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", exe, *args]
    return [exe, *args]


def terminate_pid(pid: int | str | None, *, tree: bool = True) -> bool:
    """Terminate a process started by morphd, never an arbitrary user process."""
    try:
        value = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if os.name == "nt":
        command = ["taskkill", "/PID", str(value)]
        if tree:
            command.append("/T")
        command.append("/F")
        try:
            proc = subprocess.run(
                command, capture_output=True, text=True, timeout=15, check=False
            )
            return proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    try:
        os.kill(value, 15)
        return True
    except OSError:
        return False


def pid_alive(pid: int | str | None) -> bool:
    """Return whether a process id is still running without sending a signal."""
    try:
        value = int(pid or 0)
    except (TypeError, ValueError):
        return False
    if value <= 0:
        return False
    if os.name == "nt":
        try:
            proc = subprocess.run(
                ["tasklist", "/FI", f"PID eq {value}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
            return str(value) in (proc.stdout or "")
        except (OSError, subprocess.TimeoutExpired):
            return False
    try:
        os.kill(value, 0)
        return True
    except OSError:
        return False
