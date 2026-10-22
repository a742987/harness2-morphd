"""Loopback-only HTTP API and static Web UI server."""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .. import store as engine_store
from . import API_VERSION
from .errors import ConsumerError
from .service import ConsumerService

MAX_BODY = 5 * 1024 * 1024
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _ui_root() -> Path:
    return Path(__file__).resolve().parent / "webui"


def _loopback_origin(origin: str | None, port: int) -> bool:
    if not origin:
        return True
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return parsed.scheme == "http" and parsed.hostname in _LOOPBACK_HOSTS and parsed.port == port


class LocalServer:
    def __init__(self, service: ConsumerService, *, port: int = 0):
        self.service = service
        self.port = port
        self.bootstrap_token = secrets.token_urlsafe(32)
        self.host_token = secrets.token_urlsafe(32)
        self._sessions: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.started_at = engine_store.utc_now()

    @property
    def url(self) -> str:
        port = self.httpd.server_address[1] if self.httpd else self.port
        return f"http://127.0.0.1:{port}/#bootstrap={self.bootstrap_token}"

    @property
    def base_url(self) -> str:
        port = self.httpd.server_address[1] if self.httpd else self.port
        return f"http://127.0.0.1:{port}"

    def start(self) -> str:
        server_ref = self

        class Handler(ApiHandler):
            pass

        Handler.server_ref = server_ref
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.httpd.daemon_threads = True
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="morphd-api", daemon=True)
        self._thread.start()
        runtime = self.service.store.home / "runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        engine_store.atomic_write_json(
            runtime / "server.json",
            {
                "version": 1,
                "app_version": self.service.status().get("app_version"),
                "api_version": API_VERSION,
                "port": self.httpd.server_address[1],
                "url": self.url,
                "base_url": self.base_url,
                "host_token": self.host_token,
                "pid": os.getpid(),
                "started_at": self.started_at,
            },
        )
        try:
            os.chmod(runtime / "server.json", 0o600)
        except OSError:
            pass
        self.service.store.append_log("api_started", port=self.httpd.server_address[1])
        return self.url

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        try:
            (self.service.store.home / "runtime" / "server.json").unlink()
        except FileNotFoundError:
            pass
        self.service.store.append_log("api_stopped")

    def check_bootstrap(self, token: str) -> bool:
        return hmac.compare_digest(str(token or ""), self.bootstrap_token)

    def new_session(self) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = {"csrf": csrf, "at": time.time()}
        return token, csrf

    def session(self, token: str | None) -> dict | None:
        if not token:
            return None
        with self._lock:
            return self._sessions.get(token)


class ApiHandler(BaseHTTPRequestHandler):
    server_ref: LocalServer
    server_version = "morphd/0.5"
    sys_version = ""

    # --- plumbing -------------------------------------------------------------
    def log_message(self, fmt, *args):  # keep logs free of tokens and query strings
        self.server_ref.service.store.append_log("http", method=self.command, path=self.path.split("?", 1)[0])

    def _send(self, status: int, payload, *, content_type: str = "application/json", headers: dict | None = None):
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif isinstance(payload, bytes):
            body = payload
        else:
            body = str(payload).encode("utf-8")
        headers = headers or {}
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        if not any(key.lower() == "cache-control" for key in headers):
            self.send_header("Cache-Control", "no-store")
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, payload, *, status: int = 200, headers: dict | None = None):
        if isinstance(payload, dict) and "version" not in payload:
            payload = {"version": API_VERSION, **payload}
        self._send(status, payload, headers=headers)

    def _error(self, exc: ConsumerError):
        self._json(exc.to_dict(), status=exc.http_status)

    def _security_headers(self):
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
                         "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
                         "form-action 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")

    def _loopback_request(self) -> bool:
        client = self.client_address[0] if self.client_address else ""
        if client not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return False
        host = self.headers.get("Host", "")
        if not host:
            return False
        try:
            hostname = urlsplit("http://" + host).hostname
        except ValueError:
            return False
        return hostname in _LOOPBACK_HOSTS

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ConsumerError("INVALID_REQUEST", "请求体过大。", "缩小输入后重试。")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            raise ConsumerError("INVALID_REQUEST", "请求体不是有效 JSON。", "刷新页面后重试。")
        if not isinstance(payload, dict):
            raise ConsumerError("INVALID_REQUEST", "请求体必须是 JSON 对象。", "刷新页面后重试。")
        return payload

    def _cookie(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "morphd_session":
                return unquote(value)
        return None

    def _internal_token(self) -> bool:
        token = self.headers.get("X-Morphd-Host-Token", "")
        return bool(token) and hmac.compare_digest(token, self.server_ref.host_token)

    def _authenticated(self, *, write: bool) -> bool:
        if self._internal_token():
            return True
        session = self.server_ref.session(self._cookie())
        if session is None:
            return False
        if write:
            csrf = self.headers.get("X-Morphd-CSRF", "")
            if not csrf or not hmac.compare_digest(csrf, session["csrf"]):
                raise ConsumerError("CSRF_INVALID", "请求缺少有效的 CSRF 令牌。", "刷新页面后重试。")
            if not _loopback_origin(self.headers.get("Origin"), self.server_ref.httpd.server_address[1]):
                raise ConsumerError("FORBIDDEN", "拒绝跨站请求。", "从本地 morphd 页面操作。")
        return True

    def _route(self, method: str):
        if not self._loopback_request():
            self._send(HTTPStatus.FORBIDDEN, {"error": {"code": "FORBIDDEN", "message": "仅允许本机访问。"}})
            return
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        try:
            if path.startswith("/api/"):
                if path == "/api/auth/exchange" and method == "POST":
                    self._exchange()
                    return
                write = method != "GET"
                if not self._authenticated(write=write):
                    raise ConsumerError("AUTH_REQUIRED", "需要访问令牌。", "从 harness2-morphd 启动时打印的本地地址打开页面。")
                self._api(method, path, parse_qs(parsed.query))
                return
            if method != "GET":
                raise ConsumerError("NOT_FOUND", "接口不存在。", "刷新页面。")
            self._static(path)
        except ConsumerError as exc:
            self._error(exc)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:
            self.server_ref.service.store.append_log("api_error", path=path, error=type(exc).__name__)
            self._error(ConsumerError("INTERNAL", "本地服务发生错误。", "打开诊断页查看脱敏日志。", details={}))

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    # --- auth -----------------------------------------------------------------
    def _exchange(self):
        payload = self._body()
        if not self.server_ref.check_bootstrap(str(payload.get("bootstrap_token") or "")):
            raise ConsumerError("AUTH_REQUIRED", "启动令牌无效或已过期。", "使用本次启动打印的地址。")
        token, csrf = self.server_ref.new_session()
        cookie = (
            f"morphd_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200"
        )
        self._json({"csrf_token": csrf, "user": "local"}, headers={"Set-Cookie": cookie})

    # --- API ------------------------------------------------------------------
    def _api(self, method: str, path: str, query: dict):
        service = self.server_ref.service
        parts = [unquote(part) for part in path.strip("/").split("/")][1:]  # drop 'api'

        if parts == ["status"] and method == "GET":
            self._json(service.status())
            return
        if parts == ["settings"]:
            if method == "GET":
                self._json(service.get_settings())
                return
            if method == "PUT":
                self._json(service.put_settings(self._body()))
                return
        if parts == ["settings", "reset"] and method == "POST":
            self._json(service.reset_settings())
            return

        if parts == ["providers"]:
            if method == "GET":
                self._json(service.providers_list())
                return
            if method == "POST":
                self._json(service.provider_add(self._body()), status=201)
                return
        if parts == ["providers", "test"] and method == "POST":
            payload = self._body()
            self._json(service.provider_test(None, payload))
            return
        if len(parts) == 3 and parts[0] == "providers" and parts[2] == "test" and method == "POST":
            payload = self._body()
            self._json(service.provider_test(parts[1], payload))
            return
        if len(parts) == 2 and parts[0] == "providers":
            if method == "PUT":
                self._json(service.provider_update(parts[1], self._body()))
                return
            if method == "DELETE":
                self._json(service.provider_delete(parts[1]))
                return

        if parts == ["hosts"] and method == "GET":
            self._json({"hosts": service.hosts_discover()})
            return
        if parts == ["hosts", "discover"] and method == "POST":
            self._json({"hosts": service.hosts_discover(force=True)})
            return
        if parts == ["hosts", "adapt"] and method == "POST":
            self._json(service.hosts_adapt(self._body()), status=201)
            return
        if parts == ["hosts", "uninstall"] and method == "POST":
            host = str(self._body().get("host") or "")
            self._json(service.host_uninstall(host))
            return
        if parts == ["hosts", "pick-folder"] and method == "POST":
            payload = self._body()
            self._json(service.pick_folder(str(payload.get("kind") or "install")))
            return
        if parts == ["hosts", "restore-original"] and method == "POST":
            self._json(service.restore_original(self._body()))
            return

        if parts == ["sessions"] and method == "GET":
            limit = int((query.get("limit") or ["100"])[0])
            project_id = (query.get("project") or [None])[0]
            self._json({"sessions": service.sessions_list(limit=limit, project_id=project_id)})
            return
        if parts == ["sessions", "incognito"] and method == "POST":
            payload = self._body()
            self._json(service.incognito(bool(payload.get("active")), payload.get("session_id")))
            return
        if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "analyze" and method == "POST":
            payload = self._body()
            self._json(service.session_analyze(parts[1], confirm=bool(payload.get("confirm"))))
            return
        if len(parts) == 2 and parts[0] == "sessions" and method == "GET":
            self._json(service.public_session(service.session_get(parts[1])))
            return

        if parts == ["jobs"] and method == "GET":
            state = (query.get("state") or [None])[0]
            self._json({"jobs": service.jobs_list(state=state)})
            return
        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cancel" and method == "POST":
            self._json(service.job_cancel(parts[1]))
            return

        if parts == ["candidates"] and method == "GET":
            state = (query.get("state") or [None])[0]
            project_id = (query.get("project") or [None])[0]
            self._json({"candidates": service.candidates_list(state=state, project_id=project_id)})
            return
        if len(parts) == 3 and parts[0] == "candidates" and parts[2] in (
            "apply",
            "revert",
            "feedback",
        ) and method == "POST":
            if parts[2] == "apply":
                self._json(service.candidate_apply(parts[1]))
            elif parts[2] == "revert":
                self._json(service.candidate_revert(parts[1]))
            else:
                self._json(service.candidate_feedback(parts[1], self._body()))
            return
        if len(parts) == 3 and parts[0] == "candidates" and parts[2] == "diff" and method == "GET":
            diff = service.candidate_diff(parts[1])
            self._send(200, diff, content_type="text/plain; charset=utf-8")
            return
        if len(parts) == 2 and parts[0] == "candidates" and method == "GET":
            self._json(service.candidate_get(parts[1]))
            return

        if parts == ["budget"] and method == "GET":
            project_id = (query.get("project") or [None])[0]
            self._json(service.budget_get(project_id=project_id))
            return

        if parts == ["learning", "pause"] and method == "POST":
            payload = self._body()
            self._json({"state": service.pause(payload.get("until"))})
            return
        if parts == ["learning", "resume"] and method == "POST":
            self._json({"state": service.resume()})
            return
        if parts == ["learning", "vacation"] and method == "POST":
            payload = self._body()
            self._json({"state": service.vacation(bool(payload.get("active")))})
            return

        if parts == ["privacy", "preview"] and method == "GET":
            session_id = (query.get("session_id") or [""])[0]
            self._json(service.privacy_preview(session_id))
            return
        if parts == ["privacy", "preview"] and method == "POST":
            payload = self._body()
            self._json(service.privacy_preview(str(payload.get("session_id") or "")))
            return
        if parts == ["privacy", "delete"] and method == "POST":
            self._json(service.privacy_delete(self._body()))
            return
        if parts == ["privacy", "export"] and method == "POST" and self._internal_token():
            self._json(service.privacy_export())
            return
        if parts == ["privacy", "export"] and method == "GET":
            self._json(service.privacy_export())
            return
        if len(parts) == 2 and parts[0] == "exports" and method == "GET":
            self._download_export(parts[2])
            return
        if parts == ["privacy", "retention"] and method == "POST":
            self._json(service.privacy_retention())
            return

        if parts == ["diagnostics"] and method == "GET":
            self._json(service.diagnostics())
            return
        if parts == ["diagnostics", "reset-learning"] and method == "POST":
            self._json(service.reset_learning())
            return

        if parts == ["projects"] and method == "GET":
            self._json({"projects": service.projects_list()})
            return
        if len(parts) == 2 and parts[0] == "projects":
            if method == "GET":
                self._json(service.project_get(parts[1]))
                return
            if method == "PUT":
                self._json(service.project_put(parts[1], self._body()))
                return

        if parts == ["vault", "unlock"] and method == "POST":
            payload = self._body()
            self._json(service.vault_unlock(str(payload.get("passphrase") or "")))
            return
        if parts == ["vault", "lock"] and method == "POST":
            self._json(service.vault_lock())
            return

        if parts and parts[0] == "host":
            if not self._internal_token():
                raise ConsumerError("FORBIDDEN", "宿主桥需要内部令牌。", "通过 harness2-morphd 启动宿主。")
            if parts[1:] == ["session-start"] and method == "POST":
                self._json(service.session_start(self._body()))
                return
            if parts[1:] == ["session-end"] and method == "POST":
                self._json(service.session_end(self._body()))
                return
            if parts[1:] == ["status"] and method == "GET":
                self._json(service.status())
                return

        raise ConsumerError("NOT_FOUND", "接口不存在。", "刷新页面或更新 harness2-morphd。")

    # --- static ---------------------------------------------------------------
    def _static(self, path: str):
        root = _ui_root()
        index = root / "index.html"
        if not index.is_file():
            html = (
                "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
                "<title>harness2-morphd</title></head><body>"
                "<h1>morphd</h1><p>Web UI 尚未构建。运行 "
                "npm --prefix consumer/web run build 后重启本地服务。</p>"
                "<p>API is available; this fallback page loads no external resources.</p>"
                "</body></html>"
            )
            self._send(200, html, content_type="text/html; charset=utf-8")
            return
        rel = path.lstrip("/")
        if not rel or rel == "index.html":
            target = index
        else:
            target = (root / rel).resolve()
            try:
                target.relative_to(root.resolve())
            except ValueError:
                raise ConsumerError("NOT_FOUND", "资源不存在。", "刷新页面。")
            if not target.is_file():
                if "." in Path(rel).name:
                    raise ConsumerError("NOT_FOUND", "资源不存在。", "刷新页面。")
                target = index
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        headers = {
            "Cache-Control": "public, max-age=31536000, immutable"
            if "/assets/" in str(target) or target.name != "index.html"
            else "no-store"
        }
        self._send(200, data, content_type=content_type, headers=headers)

    def _download_export(self, name: str):
        path = (self.server_ref.service.store.home / "exports" / unquote(name)).resolve()
        try:
            path.relative_to(self.server_ref.service.store.home / "exports")
        except ValueError:
            raise ConsumerError("NOT_FOUND", "导出文件不存在。", "重新导出。")
        if not path.is_file():
            raise ConsumerError("NOT_FOUND", "导出文件不存在。", "重新导出。")
        self._send(
            200,
            path.read_bytes(),
            content_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
        )
