"""PyInstaller entry point for the signed platform engine."""
import os
import sys
from pathlib import Path


def _prepare_bundled_upstream() -> None:
    if not getattr(sys, "frozen", False):
        return
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    bundled = base / "harness2"
    if bundled.is_dir():
        sys.path.insert(0, str(base))
        os.environ.setdefault("MORPHD_HARNESS2_SRC", str(base))


_prepare_bundled_upstream()

from morphd.cli.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
