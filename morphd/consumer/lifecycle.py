"""Host-bound process lifecycle and the host-event bridge."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from .. import store as engine_store
from .api import LocalServer
from .errors import ConsumerError
from .service import ConsumerService
from .session_import import end_reason_from_exit


def server_info(home: Path) -> dict | None:
    payload = engine_store.read_json(Path(home) / "runtime" / "server.json")
    return payload if isinstance(payload, dict) else None


def send_internal(event: str, payload: dict, *, home: Path | None = None, timeout: float = 10.0) -> bool:
    """Send a host event to the running loopback server; spool it if the host is gone."""
    home = home or Path(os.environ.get("MORPHD_HOME", Path.home() / ".morphd"))
    info = server_info(home)
    if info:
        token = str(info.get("host_token") or "")
        url = f"{str(info.get('base_url', '')).rstrip('/')}/api/host/{event}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Morphd-Host-Token": token,
                "Content-Length": str(len(data)),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read()
            return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
    spool = home / "spool"
    spool.mkdir(parents=True, exist_ok=True)
    path = spool / f"event-{engine_store.utc_stamp()}-{os.getpid()}-{time.time_ns()}.json"
    engine_store.atomic_write_json(
        path,
        {"type": event, "payload": payload, "at": engine_store.utc_now(), "spooled": True},
    )
    return False


def normalize_hook_payload(host: str, payload: dict) -> dict:
    """Map Claude Code hook stdin to the generic session-end payload."""
    transcript = payload.get("transcript_path") or payload.get("events_path")
    cwd = payload.get("cwd") or payload.get("project_root") or os.getcwd()
    normalized = {
        "host": host,
        "session_id": payload.get("session_id") or payload.get("sessionId"),
        "events_path": transcript,
        "project_root": cwd,
        "workspace": payload.get("workspace") or cwd,
        "end_reason": payload.get("end_reason") or payload.get("reason") or "completed",
        "user_turns": payload.get("user_turns"),
    }
    return {key: value for key, value in normalized.items() if value is not None}


class HostRuntime:
    def __init__(
        self, service: ConsumerService, *, quiet: bool = False, start_worker: bool = True
    ):
        self.service = service
        self.server = LocalServer(service)
        self.quiet = quiet
        self.start_worker = start_worker
        self._stop = False

    @property
    def url(self) -> str:
        return self.server.url

    def start(self) -> str:
        if self.start_worker:
            self.service.start()
        url = self.server.start()
        if not self.quiet:
            print(f"morphd UI: {url}", file=sys.stderr, flush=True)
        return url

    def stop(self) -> None:
        if self._stop:
            return
        self._stop = True
        try:
            self.server.stop()
        finally:
            self.service.stop()


def run_host(
    *,
    kind: str,
    binary: str,
    args: list[str],
    home: Path | None = None,
    quiet: bool = False,
    project_root: str | None = None,
) -> int:
    home = home or Path(os.environ.get("MORPHD_HOME", Path.home() / ".morphd"))
    service = ConsumerService(home)
    runtime = HostRuntime(service, quiet=quiet)
    try:
        runtime.start()
    except Exception:
        runtime.stop()
        raise
    service.host_connect(kind)
    _maybe_spawn_tray(service, home)
    if kind == "dsh":
        idle = int(os.environ.get("MORPHD_SESSION_IDLE_S", "60") or "60")
        _dsh_idle_watcher(service, home, idle_seconds=max(10, idle))
    env = {
        **os.environ,
        "MORPHD_HOME": str(home),
        "MORPHD_URL": runtime.server.base_url,
        "MORPHD_HOST_KIND": kind,
    }
    if project_root:
        env["MORPHD_PROJECT_ROOT"] = project_root
    stop_requested = {"value": False}

    def _on_signal(signum, frame):
        stop_requested["value"] = True

    previous_int = signal.signal(signal.SIGINT, _on_signal)
    previous_term = signal.signal(signal.SIGTERM, _on_signal)
    try:
        try:
            command = [binary, *args]
            if os.name == "nt" and binary.lower().endswith((".cmd", ".bat")):
                command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", *command]
            proc = subprocess.Popen(command, env=env)
        except OSError as exc:
            raise ConsumerError(
                "HOST_NOT_FOUND",
                f"无法启动宿主：{exc}",
                "检查安装路径，或在宿主页手动指定可执行文件。",
            ) from exc
        engine_stopped = False
        while True:
            try:
                code = proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if not engine_stopped and (home / "runtime" / "shell-quit").exists():
                    try:
                        (home / "runtime" / "shell-quit").unlink()
                    except OSError:
                        pass
                    try:
                        runtime.stop()
                    except Exception:
                        pass
                    engine_stopped = True
                if stop_requested["value"]:
                    # Ctrl+C was already delivered to the process group; give the host a moment.
                    try:
                        proc.terminate()
                    except OSError:
                        pass
                    try:
                        code = proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        code = proc.wait()
                    break
        end_reason = end_reason_from_exit(code)
        time.sleep(0.3)  # allow a SessionEnd hook to post before we close the server
        try:
            service.ingest_latest_for_host(kind, end_reason=end_reason)
        except Exception as exc:
            service.store.append_log("host_exit_ingest_failed", host=kind, error=str(exc))
        service.host_disconnect(kind)
        return int(code or 0)
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        runtime.stop()


def _maybe_spawn_tray(service: ConsumerService, home: Path) -> subprocess.Popen | None:
    try:
        settings = service.settings_manager.load()
        if not settings.get("cadence", {}).get("tray_enabled", True):
            return None
        if os.environ.get("MORPHD_TRAY", "1") == "0":
            return None
        if os.name != "nt" and sys.platform != "darwin":
            if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
                return None
        import pystray  # noqa: F401
    except Exception:
        return None
    engine = os.environ.get("MORPHD_ENGINE_BIN", "").strip()
    if engine:
        command = [engine]
    elif getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        command = [sys.executable, "-m", "morphd"]
    try:
        return subprocess.Popen(
            [*command, "tray", "--home", str(home)],
            env={**os.environ, "MORPHD_HOME": str(home)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None


def run_server(home: Path | None = None, *, quiet: bool = False, config_only: bool = False) -> int:
    home = home or Path(os.environ.get("MORPHD_HOME", Path.home() / ".morphd"))
    service = ConsumerService(home)
    runtime = HostRuntime(service, quiet=quiet, start_worker=not config_only)
    try:
        runtime.start()
        while True:
            if (home / "runtime" / "shell-quit").exists():
                try:
                    (home / "runtime" / "shell-quit").unlink()
                except OSError:
                    pass
                return 0
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    finally:
        runtime.stop()


def _dsh_idle_watcher(service: ConsumerService, home: Path, *, idle_seconds: int) -> threading.Thread:
    """Fallback for dsh when no native session-close event is available.

    A session is considered ended only when its log ends with a turn/end record and has
    been unchanged for ``idle_seconds``. This follows the PRD's conservative fallback and
    never treats an in-progress turn as finished.
    """
    import time as _time
    from pathlib import Path as _Path

    from .. import tasksource
    from .session_import import END_REASONS

    seen: dict[str, tuple[float, int]] = {}
    started_at = _time.time()

    def loop():
        while True:
            info = server_info(home)
            if info is None:
                return
            try:
                install = service.hosts.record_for("dsh") or {}
                config = _Path(str(install.get("config_dir") or "")).expanduser()
                root = _Path(os.environ.get("DSH_HOME", str(config))) / "sessions"
                if not root.is_dir():
                    _time.sleep(5)
                    continue
                now = _time.time()
                for path in tasksource.discover_sessions(root):
                    try:
                        st = path.stat()
                    except OSError:
                        continue
                    key = str(path)
                    marker = seen.get(key)
                    if marker == (st.st_mtime, st.st_size):
                        continue
                    seen[key] = (st.st_mtime, st.st_size)
                    if st.st_mtime < started_at - 5:
                        continue
                    if now - st.st_mtime < idle_seconds:
                        continue
                    try:
                        finding = tasksource.read_session(path)
                    except Exception:
                        continue
                    if not finding.events:
                        continue
                    last = finding.events[-1]
                    if last.get("type") != "turn/end":
                        continue
                    reason = (last.get("data") or {}).get("reason") or {}
                    kind = reason.get("kind") if isinstance(reason, dict) else ""
                    if kind in ("success", "end_turn", "stop"):
                        kind = "completed"
                    if kind not in END_REASONS:
                        continue
                    cwd = finding.cwd or str(_Path.home())
                    try:
                        service.session_end(
                            {
                                "host": "dsh",
                                "session_id": finding.session_id,
                                "events_path": str(path),
                                "workspace": cwd,
                                "project_root": cwd,
                                "end_reason": kind,
                            }
                        )
                    except Exception as exc:
                        service.store.append_log("dsh_idle_ingest_failed", path=str(path), error=str(exc))
            except Exception as exc:
                service.store.append_log("dsh_watch_error", error=str(exc))
            _time.sleep(3)

    thread = threading.Thread(target=loop, name="morphd-dsh-watch", daemon=True)
    thread.start()
    return thread
