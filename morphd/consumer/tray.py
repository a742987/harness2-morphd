"""Runtime tray helper for the NPM/browser path.

The tray is optional and exists only while the host server is alive. It reads the host
token from the 0600 runtime file, never caches keys/session text, and exits on its own
when the host disconnects.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from .. import store as engine_store
from .lifecycle import server_info
from .service import ConsumerService


def _http(home: Path, method: str, path: str, payload: dict | None = None) -> dict | None:
    info = server_info(home)
    if not info:
        return None
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        str(info.get("base_url", "")).rstrip("/") + path,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-Morphd-Host-Token": str(info.get("host_token", "")),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _icon_image():
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 62, 62), radius=14, fill="#76B900", outline="#000000", width=5)
    draw.rounded_rectangle((18, 16, 46, 48), radius=8, fill="#000000")
    return image


def run_tray(home: Path) -> int:
    try:
        settings = ConsumerService(home).settings_manager.load()
        if not settings.get("cadence", {}).get("tray_enabled", True):
            print("morphd tray: disabled in settings; using the browser UI only.", file=sys.stderr)
            return 0
    except Exception:
        pass
    if not sys.platform.startswith("win") and not sys.platform == "darwin":
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            print("morphd tray: no desktop session; using the browser UI only.", file=sys.stderr)
            return 0
    try:
        import pystray  # type: ignore
    except Exception:
        print(
            "morphd tray: optional pystray/Pillow dependencies are not installed; "
            "using the browser UI only. Install morphd[tray] for a tray helper.",
            file=sys.stderr,
        )
        return 0
    info = server_info(home)
    if not info:
        print("morphd tray: no running host server; nothing to attach to.", file=sys.stderr)
        return 0
    status = _http(home, "GET", "/api/status") or {}
    learning = status.get("learning") or {}
    budget = (status.get("budget") or {}).get("monthly") or {}
    state = {"confirm_stop": 0.0, "text": ""}

    def refresh_text() -> str:
        if learning.get("paused"):
            return "morphd · 已暂停"
        return "morphd · 学习中"

    def open_ui(icon, item):
        webbrowser.open(str(info.get("url", info.get("base_url", ""))))

    def call_pause(until: str):
        _http(home, "POST", "/api/learning/pause", {"until": until})

    def call_resume(icon=None, item=None):
        _http(home, "POST", "/api/learning/resume", {})
        icon.update_menu()

    def incognito(icon=None, item=None):
        state["text"] = "morphd · 下一次会话隐身"
        _http(home, "POST", "/api/sessions/incognito", {"active": True})
        icon.update_menu()

    def quit_shell(icon, item):
        icon.stop()

    def stop_learning(icon, item):
        now = time.monotonic()
        if now - state["confirm_stop"] > 5:
            state["confirm_stop"] = now
            state["text"] = "morphd · 再次点击“停止学习并退出”确认"
            icon.update_menu()
            return
        marker = home / "runtime" / "shell-quit"
        engine_store.atomic_write_text(marker, engine_store.utc_now())
        try:
            os.chmod(marker, 0o600)
        except OSError:
            pass
        icon.stop()

    def status_text(icon, item):
        state["text"] = (
            f"morphd · {'已暂停' if learning.get('paused') else '学习中'}\n"
            f"本月 ${float(budget.get('spent') or 0):.2f} / ${float(budget.get('limit') or 0):.2f}"
        )
        return state["text"]

    def build_menu():
        import pystray

        return pystray.Menu(
            pystray.MenuItem(state["text"] or refresh_text(), lambda icon, item: None, enabled=False),
            pystray.MenuItem("打开 morphd", open_ui, default=True),
            pystray.MenuItem("打开设置", lambda icon, item: webbrowser.open(str(info.get("url", "")))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("状态", status_text),
            pystray.MenuItem(
                f"本月花费 ${float(budget.get('spent') or 0):.2f}",
                lambda icon, item: None,
                enabled=False,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("暂停 1 小时", lambda icon, item: call_pause("1h")),
            pystray.MenuItem("暂停 1 天", lambda icon, item: call_pause("1d")),
            pystray.MenuItem("暂停 1 周", lambda icon, item: call_pause("1w")),
            pystray.MenuItem("恢复学习", call_resume),
            pystray.MenuItem("下一次会话隐身", incognito),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出托盘（宿主继续运行）", quit_shell),
            pystray.MenuItem("停止学习并退出（二次确认）", stop_learning),
        )

    icon = pystray.Icon("morphd", _icon_image(), refresh_text(), build_menu())
    stop = threading.Event()

    def watcher():
        while not stop.wait(5):
            current = server_info(home)
            if current is None:
                icon.stop()
                return

    thread = threading.Thread(target=watcher, name="morphd-tray-watch", daemon=True)
    thread.start()
    try:
        icon.run()
    finally:
        stop.set()
    return 0
