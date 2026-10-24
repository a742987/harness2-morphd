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
from .. import winproc
from .api import LocalServer
from .errors import ConsumerError
from .service import ConsumerService
from .session_import import end_reason_from_exit


def server_info(home: Path) -> dict | None:
    runtime = Path(home) / "runtime"
    payload = engine_store.read_json(runtime / "server.json")
    if isinstance(payload, dict) and winproc.pid_alive(payload.get("pid")):
        return payload
    servers = runtime / "servers"
    candidates = []
    if servers.is_dir():
        for path in servers.glob("*.json"):
            info = engine_store.read_json(path)
            if not isinstance(info, dict):
                continue
            if not winproc.pid_alive(info.get("pid")):
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            candidates.append(info)
    if not candidates:
        return None
    newest = max(candidates, key=lambda item: str(item.get("started_at") or ""))
    engine_store.atomic_write_json(runtime / "server.json", newest)
    return newest


def _host_record_path(home: Path, kind: str) -> Path:
    return Path(home) / "runtime" / "hosts" / f"{kind}-{os.getpid()}.json"


def _register_host(home: Path, kind: str, child_pid: int) -> Path:
    path = _host_record_path(home, kind)
    engine_store.atomic_write_json(
        path,
        {
            "version": 1,
            "host": kind,
            "pid": os.getpid(),
            "child_pid": int(child_pid),
            "started_at": engine_store.utc_now(),
        },
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _unregister_host(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _active_host_count(home: Path) -> int:
    root = Path(home) / "runtime" / "hosts"
    count = 0
    if not root.is_dir():
        return 0
    for path in root.glob("*.json"):
        payload = engine_store.read_json(path)
        if isinstance(payload, dict) and (
            winproc.pid_alive(payload.get("pid")) or winproc.pid_alive(payload.get("child_pid"))
        ):
            count += 1
        else:
            try:
                path.unlink()
            except OSError:
                pass
    return count


def _tray_pid_path(home: Path) -> Path:
    return Path(home) / "runtime" / "tray.pid"


def _live_tray_pid(home: Path) -> int | None:
    path = _tray_pid_path(home)
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if winproc.pid_alive(pid):
        return pid
    try:
        path.unlink()
    except OSError:
        pass
    return None


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
        self,
        service: ConsumerService,
        *,
        quiet: bool = False,
        start_worker: bool = True,
        open_browser: bool = False,
    ):
        self.service = service
        self.server = LocalServer(service)
        self.quiet = quiet
        self.start_worker = start_worker
        self.open_browser = open_browser
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
        if self.open_browser:
            import webbrowser

            threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
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
    previous_break = signal.signal(signal.SIGBREAK, _on_signal) if hasattr(signal, "SIGBREAK") else None
    host_record: Path | None = None
    try:
        try:
            proc = subprocess.Popen(winproc.command_line(binary, args), env=env)
            host_record = _register_host(home, kind, proc.pid)
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
        if previous_break is not None:
            signal.signal(signal.SIGBREAK, previous_break)
        if host_record is not None:
            _unregister_host(host_record)
        runtime.stop()
        if _active_host_count(home) == 0:
            tray_pid = _live_tray_pid(home)
            if tray_pid is not None:
                winproc.terminate_pid(tray_pid)
                try:
                    _tray_pid_path(home).unlink()
                except OSError:
                    pass


def _maybe_spawn_tray(service: ConsumerService, home: Path) -> subprocess.Popen | None:
    try:
        settings = service.settings_manager.load()
        if not settings.get("cadence", {}).get("tray_enabled", True):
            return None
        if os.environ.get("MORPHD_TRAY", "1") == "0":
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
        with engine_store.file_lock(home / "runtime" / "tray.spawn.lock"):
            if _live_tray_pid(home) is not None:
                return None
            proc = subprocess.Popen(
                [*command, "tray", "--home", str(home)],
                env={**os.environ, "MORPHD_HOME": str(home)},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                _tray_pid_path(home).write_text(str(proc.pid), encoding="utf-8")
            except OSError:
                pass
            return proc
    except Exception:
        return None


def run_server(
    home: Path | None = None,
    *,
    quiet: bool = False,
    config_only: bool = False,
    open_browser: bool = False,
    exit_on_setup: bool = False,
) -> int:
    home = home or Path(os.environ.get("MORPHD_HOME", Path.home() / ".morphd"))
    setup_marker = Path(home) / "runtime" / "setup-complete"
    if exit_on_setup:
        try:
            setup_marker.unlink()
        except FileNotFoundError:
            pass
    service = ConsumerService(home)
    runtime = HostRuntime(
        service,
        quiet=quiet,
        start_worker=not config_only,
        open_browser=open_browser,
    )
    try:
        runtime.start()
        while True:
            if (home / "runtime" / "shell-quit").exists():
                try:
                    (home / "runtime" / "shell-quit").unlink()
                except OSError:
                    pass
                return 0
            if exit_on_setup and setup_marker.exists():
                try:
                    setup_marker.unlink()
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
                        try:
                            active = tasksource.read_session(path)
                            if active.events and active.events[-1].get("type") != "turn/end":
                                service.session_start(
                                    {"host": "dsh", "session_id": active.session_id}
                                )
                        except Exception:
                            pass
                        continue
                    try:
                        finding = tasksource.read_session(path)
                    except Exception:
                        continue
                    if not finding.events:
                        continue
                    last = finding.events[-1]
                    if last.get("type") != "turn/end":
                        # A session is still in progress; focus mode must suppress
                        # proactive suggestions until the session-end event arrives.
                        try:
                            service.session_start(
                                {"host": "dsh", "session_id": finding.session_id}
                            )
                        except Exception:
                            pass
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
