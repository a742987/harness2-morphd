"""Platform policy: Windows 10/11 x64 is the only supported runtime."""

from __future__ import annotations

import platform
import sys

SUPPORTED_PLATFORM = "win32-x64"


def is_windows_x64() -> bool:
    return sys.platform == "win32" and platform.machine().lower() in {"amd64", "x86_64"}


def require_windows() -> None:
    if not is_windows_x64():
        raise SystemExit(
            "harness2-morphd supports Windows 10/11 x64 only; "
            f"this is {sys.platform}-{platform.machine() or 'unknown'}."
        )
    parts = platform.version().split(".")
    try:
        major = int(parts[0])
    except (IndexError, ValueError):
        major = 0
    if major < 10:
        raise SystemExit(
            f"harness2-morphd requires Windows 10 or 11; this is Windows {platform.version()}."
        )
