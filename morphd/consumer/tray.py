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
from ..platform_support import require_windows
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


def _icon_image(*, badge: bool = False):
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 62, 62), radius=14, fill="#76B900", outline="#000000", width=5)
    draw.rounded_rectangle((18, 16, 46, 48), radius=8, fill="#000000")
    if badge:
        # A single small dot, never a number or a flashing/red badge.
        draw.ellipse((46, 6, 58, 18), fill="#76B900", outline="#000000", width=2)
    return image


def run_tray(home: Path) -> int:
    require_windows()
    try:
        settings = ConsumerService(home).settings_manager.load()
        if not settings.get("cadence", {}).get("tray_enabled", True):
            print("morphd tray: disabled in settings; using the browser UI only.", file=sys.stderr)
            return 0
    except Exception:
        pass
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
    state = {"confirm_stop": 0.0, "text": "", "status": {}, "active_session": "", "badge": False}

    def refresh_status() -> None:
        status = _http(home, "GET", "/api/status") or {}
        state["status"] = status
        learning = status.get("learning") or {}
        current = (learning.get("active_sessions") or [{}])[0] or {}
        state["active_session"] = str(current.get("session_id") or "")
        settings = _http(home, "GET", "/api/settings") or {}
        cadence = settings.get("cadence") or {}
        badge = False
        if cadence.get("suggestion_badge") is True:
            candidates = _http(home, "GET", "/api/candidates?state=needs_apply") or []
            badge = isinstance(candidates, list) and any(
                isinstance(item, dict) and item.get("id") and item.get("presented")
                for item in candidates
            )
        state["badge"] = bool(badge)

    refresh_status()

    def status_label() -> str:
        status = state.get("status") or {}
        tray = (status.get("tray") or {}).get("status") or "not_connected"
        labels = {
            "learning": "学习中",
            "paused": "已暂停",
            "incognito": "本次隐身",
            "budget_exhausted": "预算用完",
            "needs_setup": "需要配置模型",
            "needs_attention": "需要处理",
            "model_error": "模型错误",
            "not_connected": "未连接宿主",
            "idle": "待命",
        }
        return labels.get(tray, "未连接宿主")

    def refresh_text() -> str:
        status = state.get("status") or {}
        budget = (status.get("budget") or {}).get("monthly") or {}
        return (
            f"harness2-morphd · {status_label()}\n"
            f"本月 ${float(budget.get('spent') or 0):.2f} / ${float(budget.get('limit') or 0):.2f}"
        )

    def open_ui(icon, item):
        webbrowser.open(str(info.get("url", info.get("base_url", ""))))

    def call_pause(until: str):
        _http(home, "POST", "/api/learning/pause", {"until": until})

    def call_resume(icon=None, item=None):
        _http(home, "POST", "/api/learning/resume", {})
        icon.update_menu()

    def incognito(icon=None, item=None):
        active_session = state.get("active_session") or ""
        state["text"] = (
            "harness2-morphd · 本次会话隐身"
            if active_session
            else "harness2-morphd · 下一次会话隐身"
        )
        payload = {"active": True}
        if active_session:
            payload["session_id"] = active_session
        _http(home, "POST", "/api/sessions/incognito", payload)
        refresh_status()
        icon.update_menu()

    def quit_shell(icon, item):
        icon.stop()

    def stop_learning(icon, item):
        now = time.monotonic()
        if now - state["confirm_stop"] > 5:
            state["confirm_stop"] = now
            state["text"] = "harness2-morphd · 再次点击“停止学习并退出”确认"
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
        refresh_status()
        state["text"] = refresh_text()
        return state["text"]

    def incognito_label(icon, item):
        return "本次会话立即隐身" if state.get("active_session") else "下一次会话隐身"

    def budget_label(icon, item):
        status = state.get("status") or {}
        budget = (status.get("budget") or {}).get("monthly") or {}
        return f"本月花费 ${float(budget.get('spent') or 0):.2f}"

    def host_menu():
        import pystray

        hosts = (state.get("status") or {}).get("hosts") or []
        items = [
            pystray.MenuItem(
                f"{item.get('label') or item.get('host')} · "
                f"{'已连接' if item.get('connected') else '未连接'}",
                lambda icon, entry: None,
                enabled=False,
            )
            for item in hosts
            if isinstance(item, dict)
        ]
        return pystray.Menu(*(items or [pystray.MenuItem("未连接宿主", lambda icon, entry: None, enabled=False)]))

    def build_menu():
        import pystray

        return pystray.Menu(
            pystray.MenuItem(lambda icon, item: refresh_text(), lambda icon, item: None, enabled=False),
            pystray.MenuItem("打开 harness2-morphd", open_ui, default=True),
            pystray.MenuItem("打开设置", lambda icon, item: webbrowser.open(str(info.get("url", "")))),
            pystray.MenuItem("打开宿主（请在任务栏切换）", lambda icon, item: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("状态", status_text, submenu=host_menu()),
            pystray.MenuItem(budget_label, lambda icon, item: None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("暂停 1 小时", lambda icon, item: call_pause("1h")),
            pystray.MenuItem("暂停 1 天", lambda icon, item: call_pause("1d")),
            pystray.MenuItem("暂停 1 周", lambda icon, item: call_pause("1w")),
            pystray.MenuItem("恢复学习", call_resume),
            pystray.MenuItem(incognito_label, incognito),
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
            refresh_status()
            try:
                icon.icon = _icon_image(badge=bool(state.get("badge")))
                icon.update_menu()
            except Exception:
                pass

    thread = threading.Thread(target=watcher, name="morphd-tray-watch", daemon=True)
    thread.start()
    try:
        icon.run()
    finally:
        stop.set()
        try:
            pid_path = home / "runtime" / "tray.pid"
            if pid_path.read_text(encoding="utf-8").strip() == str(os.getpid()):
                pid_path.unlink()
        except OSError:
            pass
    return 0
