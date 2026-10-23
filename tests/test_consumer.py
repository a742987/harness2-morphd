from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from morphd.consumer.api import LocalServer
from morphd.consumer.attention import AttentionManager
from morphd.consumer.budget import ConsumerBudget
from morphd.consumer.errors import ConsumerError
from morphd.consumer.keychain import KeyVault
from morphd.consumer.policy import check_candidate
from morphd.consumer.providers import ProviderClient, ProvidersManager
from morphd.consumer.service import ConsumerService
from morphd.consumer.session_import import SessionContext
from morphd.consumer.settings import validate_settings
from morphd.consumer.store import ConsumerStore


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MORPHD_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MORPHD_VAULT_PASSPHRASE", "test-passphrase")
    monkeypatch.setattr("morphd.consumer.keychain._keyring_available", lambda: False)
    return tmp_path / "home"


class FakeProvider:
    def __init__(self):
        self.requests = []
        self.calls = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def _json(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer test-key":
                    self._json(401, {"error": "bad key"})
                    return
                if self.path.endswith("/models"):
                    self._json(200, {"data": [{"id": "fake-model"}]})
                    return
                self._json(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append(payload)
                if self.headers.get("Authorization") != "Bearer test-key":
                    self._json(401, {"error": "bad key"})
                    return
                outer.calls += 1
                messages = payload.get("messages") or []
                has_tool_result = any(message.get("role") == "tool" for message in messages)
                if payload.get("tools") and not has_tool_result:
                    self._json(
                        200,
                        {
                            "choices": [
                                {
                                    "message": {
                                        "role": "assistant",
                                        "content": "",
                                        "tool_calls": [
                                            {
                                                "id": "call-1",
                                                "type": "function",
                                                "function": {
                                                    "name": "write_file",
                                                    "arguments": json.dumps(
                                                        {
                                                            "path": "AGENTS.md",
                                                            "content": "# Working guidance\n\n- Run the project checks before finishing.\n",
                                                        }
                                                    ),
                                                },
                                            }
                                        ],
                                    },
                                    "finish_reason": "tool_calls",
                                }
                            ],
                            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                        },
                    )
                    return
                self._json(
                    200,
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "Added a build verification reminder to AGENTS.md.",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
                    },
                )

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture()
def fake_provider():
    server = FakeProvider()
    yield server
    server.close()


def _provider_payload(server: FakeProvider, *, provider_id="p_test"):
    return {
        "id": provider_id,
        "label": "Fake",
        "protocol": "openai-compatible",
        "base_url": server.base_url,
        "model": "fake-model",
        "context_window": 128000,
        "max_output": 4096,
        "supports_tools": True,
        "supports_json": True,
        "data_policy_ack": True,
        "data_policy": "local test",
        "price": {
            "input_per_million": 1.0,
            "cached_input_per_million": 0.1,
            "cache_write_per_million": 1.0,
            "output_per_million": 2.0,
            "reasoning_per_million": 0.0,
            "per_call_usd": 0.0,
            "confirmed": True,
        },
    }


def _fake_dsh(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "dsh.cmd"
    exe.write_bytes(
        b'@echo off\r\nif "%1"=="--version" (echo dsh 0.2.1-fake & exit /b 0)\r\nexit /b 0\r\n'
    )
    config = tmp_path / "dsh-home"
    (config / "profiles" / "default").mkdir(parents=True)
    (config / "profiles" / "default" / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DSH_HOME", str(config))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return exe, config


def _install_and_project(tmp_path, monkeypatch, home):
    exe, config = _fake_dsh(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Old guidance\n\n- Keep it minimal.\n", encoding="utf-8")
    service = ConsumerService(home)
    candidate = service.hosts.validate_manual(
        "dsh", install_path=str(exe), config_path=str(config), project_root=str(project)
    )
    record = service.hosts_adapt({"candidate": candidate, "project_root": str(project)})
    return service, project, record


def test_settings_defaults_and_validation():
    settings = validate_settings({})
    assert settings["trust_level"] == "advisor"
    assert settings["change_appetite"] == "conservative"
    assert settings["auto_apply"] is False
    assert settings["budget"]["monthly_usd"] == 5.0
    assert settings["cadence"]["weekly_cap"] == 2
    with pytest.raises(Exception):
        validate_settings({"auto_apply": True})  # advisor may not auto-apply
    with pytest.raises(Exception):
        validate_settings({"trust_level": "autopilot", "auto_apply": True, "autopilot_ack": False})
    ok = validate_settings(
        {
            "trust_level": "autopilot",
            "auto_apply": True,
            "autopilot_ack": True,
            "undo_days": 30,
            "budget": {"monthly_usd": 5, "per_session_usd": 0.5, "per_job_usd": 1.0},
        }
    )
    assert ok["change_appetite"] == "conservative"


def test_encrypted_vault_roundtrip(home):
    store = ConsumerStore(home)
    vault = KeyVault(store.home)
    ref, hint = vault.set("p1", "sk-test-secret")
    assert ref.startswith("vault:")
    assert hint.endswith("cret")
    assert vault.get(ref) == "sk-test-secret"
    raw = (store.home / "vault.enc").read_text(encoding="utf-8")
    assert "sk-test-secret" not in raw
    vault.delete(ref)
    assert vault.get(ref) is None


def test_provider_complete_and_test(home, fake_provider):
    store = ConsumerStore(home)
    manager = ProvidersManager(store)
    provider = manager.add(_provider_payload(fake_provider), "test-key")
    assert "api_key" not in provider and "key_ref" not in provider
    result = manager.test(provider["id"], None)
    assert result["ok"] is True
    assert result["tool_mode_possible"] is True
    bundle = __import__("morphd.consumer.privacy", fromlist=["export_bundle"]).export_bundle(store)
    assert "test-key" not in json.dumps(bundle)
    assert "sk-test-secret" not in json.dumps(bundle)
    client = ProviderClient({**manager.get(provider["id"]), "model": "fake-model"}, "test-key")
    completion = client.complete([{"role": "user", "content": "hi"}], tools=[])
    assert completion.text
    assert completion.usage["present"] is True
    assert completion.cost_usd is not None


def test_budget_three_tiers(home):
    store = ConsumerStore(home)
    budget = ConsumerBudget(store)
    budget.precheck(0.1, monthly_limit=1.0, session_limit=0.5, job_limit=0.2,
                    session_id="s", job_id="j", alert_ratio=0.8)
    reservation = budget.reserve(0.2, kind="analysis", label="fake", session_id="s", job_id="j")
    with pytest.raises(ConsumerError) as exc:
        budget.precheck(0.1, monthly_limit=1.0, session_limit=0.5, job_limit=0.2,
                        session_id="s", job_id="j", alert_ratio=0.8)
    assert exc.value.code == "BUDGET_EXCEEDED"
    budget.charge(0.1, kind="analysis", label="fake", reservation=reservation,
                  session_id="s", job_id="j")
    snap = budget.snapshot(monthly_limit=1.0, session_limit=0.5, job_limit=0.2,
                           session_id="s", job_id="j")
    assert snap.session_spent == pytest.approx(0.1)
    budget.release(reservation)
    with pytest.raises(ConsumerError) as exc:
        budget.precheck(None, monthly_limit=1.0, session_limit=0.5, job_limit=0.2,
                        session_id="s", job_id="j")
    assert exc.value.code == "BUDGET_UNKNOWN_PRICE"


def test_attention_quiet_cap_feedback_incognito(home):
    manager = AttentionManager(ConsumerStore(home))
    settings = validate_settings({})
    late = __import__("datetime").datetime(2026, 10, 22, 23, 0).astimezone()
    decision = manager.presentation(settings=settings, project_id="p", now=late)
    assert decision == {"present": False, "reason": "quiet_hours"}
    early = late.replace(hour=10)
    decision = manager.presentation(settings=settings, project_id="p", confidence="high", now=early)
    assert decision["present"] is True
    manager.claim_presentation("p", now=early)
    manager.claim_presentation("p", now=early)
    decision = manager.presentation(settings=settings, project_id="p", confidence="high", now=early)
    assert decision["reason"] == "weekly_cap"
    stats = manager.record_feedback("p", "ignore")
    assert stats["consecutive_ignores"] == 1
    manager.record_feedback("p", "ignore")
    stats = manager.record_feedback("p", "ignore")
    assert stats["downranked"] is True
    manager.set_incognito(True, "s1")
    assert manager.is_incognito("s1") is True
    assert manager.consume_incognito("s1") is True
    manager.pause("1d")
    assert manager.is_paused() is True
    manager.resume()
    assert manager.is_paused() is False


def test_host_discovery_adapt_uninstall(tmp_path, monkeypatch, home):
    service, project, record = _install_and_project(tmp_path, monkeypatch, home)
    assert record["status"] in ("adapted", "beta")
    assert Path(record["wrapper"]).is_file()
    assert service.hosts.record_for("dsh")["version"] == "0.2.1"
    candidates = service.hosts_discover()
    assert any(c.get("installed") for c in candidates)
    result = service.host_uninstall("dsh")
    assert result["ok"] is True
    assert service.hosts.record_for("dsh") is None
    assert not Path(record["wrapper"]).exists()


def test_policy_rejects_extra_files_and_risk_terms():
    base = {"AGENTS.md": "old\n"}
    candidate = {
        "AGENTS.md": "new\n",
        "scripts/new.py": "print(1)\n",
    }
    check = check_candidate(runtime="dsh", base_tree=base, candidate_tree=candidate,
                            appetite_name="conservative")
    assert check.ok is False
    risky = {"AGENTS.md": "old\n", "CLAUDE.md": "bypass approvals\n"}
    check = check_candidate(runtime="dsh", base_tree=base, candidate_tree=risky,
                            appetite_name="balanced")
    assert check.ok is False
    traversal = {"AGENTS.md": "old\n", "../outside.md": "x\n"}
    check = check_candidate(runtime="dsh", base_tree=base, candidate_tree=traversal,
                            appetite_name="bold")
    assert check.ok is False


def test_dsh_session_import(tmp_path):
    session_file = tmp_path / "sess" / "session.v1.jsonl"
    session_file.parent.mkdir()
    rows = [
        {"type": "session", "id": "sess-1", "cwd": str(tmp_path), "createdAt": "2026-10-22T10:00:00Z"},
        {"type": "user/message", "data": {"message": {"source": {"kind": "user"},
         "content": [{"type": "text", "text": "Fix the build"}]}}},
        {"type": "turn/end", "data": {"reason": {"kind": "error"}}},
        {"type": "user/message", "data": {"message": {"source": {"kind": "user"},
         "content": [{"type": "text", "text": "No, use make not npm"}]}}},
        {"type": "turn/end", "data": {"reason": {"kind": "completed"}}},
    ]
    session_file.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    from morphd.consumer.session_import import ingest

    context = ingest(
        host="dsh",
        events_path=session_file,
        workspace=tmp_path,
        project_root=tmp_path,
    )
    assert context.session_id == "sess-1"
    assert len(context.user_turns) == 2
    assert context.user_turns[1]["corrective"] is True
    assert context.signals["corrections"] == 1
    assert context.end_reason == "completed"


def test_candidate_apply_revert_end_to_end(tmp_path, monkeypatch, home, fake_provider):
    service, project, record = _install_and_project(tmp_path, monkeypatch, home)
    provider = service.providers.add(_provider_payload(fake_provider), "test-key")
    settings = service.settings_manager.load()
    settings["models"]["proposer"] = f"{provider['id']}/fake-model"
    settings["models"]["composer"] = f"{provider['id']}/fake-model"
    settings["budget"] = {"monthly_usd": 5, "per_session_usd": 0.5, "per_job_usd": 1.0,
                          "alert_ratio": 0.8, "confirm_before_analysis": False}
    service.settings_manager.save(settings)
    workspace = service.store.home / "session-source"
    workspace.mkdir()
    (workspace / "main.py").write_text("print('hello')\n", encoding="utf-8")
    context = SessionContext(
        session_id="sess-e2e",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        started_at="2026-10-22T10:00:00+00:00",
        ended_at="2026-10-22T10:05:00+00:00",
        end_reason="completed",
        user_turns=[
            {"text": "Fix the build", "corrective": False},
            {"text": "No, run the project checks first", "corrective": True},
        ],
        signals={"achieved": True, "delivered": True, "corrections": 2, "tool_loops": 0},
        trajectory_summary="[USER] Fix the build\n[ASSISTANT] fixed",
    )
    result = service.jobs.handle_session_end(context, source_workspace=workspace)
    assert result["queued"] is True
    session = service.store.read_record("sessions", "sess-e2e")
    job = service.store.read_record("jobs", result["job_id"])
    outcome = service.jobs.analyzer.run(job, session)
    assert outcome["state"] == "needs_apply", outcome
    candidate = service.candidates.get(outcome["candidate_id"])
    assert "AGENTS.md" in candidate["files_changed"]
    assert candidate["summary"]
    assert Path(candidate["diff_path"]).is_file()
    applied = service.candidates.apply(candidate["id"], confirm=True)
    assert applied["state"] == "applied"
    assert "project checks" in (project / "AGENTS.md").read_text(encoding="utf-8")
    reverted = service.candidates.revert(candidate["id"], confirm=True)
    assert reverted["state"] == "reverted"
    assert "project checks" not in (project / "AGENTS.md").read_text(encoding="utf-8")
    assert "Old guidance" in (project / "AGENTS.md").read_text(encoding="utf-8")


def test_session_incognito_and_idempotency(tmp_path, monkeypatch, home):
    service, project, record = _install_and_project(tmp_path, monkeypatch, home)
    workspace = tmp_path / "project"
    context = SessionContext(
        session_id="sess-idem",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        ended_at="2026-10-22T11:00:00+00:00",
        user_turns=[{"text": "hello", "corrective": False}],
        signals={"achieved": True, "corrections": 0, "tool_loops": 0},
    )
    first = service.jobs.handle_session_end(context, source_workspace=workspace)
    second = service.jobs.handle_session_end(context, source_workspace=workspace)
    assert first["job_id"] == second["job_id"]
    assert len([j for j in service.store.list_records("jobs") if j.get("session_id") == "sess-idem"]) == 1
    service.attention.set_incognito(True, "sess-secret")
    secret = SessionContext(
        session_id="sess-secret",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        user_turns=[{"text": "secret", "corrective": False}],
    )
    result = service.jobs.handle_session_end(secret, source_workspace=workspace)
    assert result["captured"] is False
    assert service.store.read_record("sessions", "sess-secret") is None


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.cookie = ""
        self.csrf = ""

    def request(self, method: str, path: str, payload=None, *, csrf=True, host_token=None):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if csrf and self.csrf and method != "GET":
            headers["X-Morphd-CSRF"] = self.csrf
        if host_token:
            headers["X-Morphd-Host-Token"] = host_token
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read()
                if resp.headers.get("Set-Cookie"):
                    self.cookie = resp.headers["Set-Cookie"].split(";", 1)[0]
                return resp.status, json.loads(body or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


def test_local_api_auth_csrf_and_headers(tmp_path, monkeypatch, home):
    service = ConsumerService(home)
    server = LocalServer(service)
    server.start()
    base = server.base_url
    client = ApiClient(base)
    try:
        status, payload = client.request("GET", "/api/status")
        assert status == 401
        status, payload = client.request(
            "POST", "/api/auth/exchange", {"bootstrap_token": server.bootstrap_token}
        )
        assert status == 200
        client.csrf = payload["csrf_token"]
        status, payload = client.request("GET", "/api/status")
        assert status == 200 and payload["app_version"]
        status, payload = client.request("PUT", "/api/settings", {"cadence": {"weekly_cap": 3}})
        assert status == 200
        assert payload["cadence"]["weekly_cap"] == 3
        client.csrf = "wrong"
        status, payload = client.request("PUT", "/api/settings", {"cadence": {"weekly_cap": 1}})
        assert status == 403
        req = urllib.request.Request(base + "/api/status", headers={"Host": "evil.example"})
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 403
        req = urllib.request.Request(base + "/")
        with urllib.request.urlopen(req, timeout=5) as resp:
            csp = resp.headers.get("Content-Security-Policy", "")
            assert "default-src 'self'" in csp
            assert resp.headers.get("X-Frame-Options") == "DENY"
    finally:
        server.stop()


def test_incognito_tombstone_is_idempotent(tmp_path, monkeypatch, home):
    service, project, _record = _install_and_project(tmp_path, monkeypatch, home)
    workspace = tmp_path / "project"
    service.attention.set_incognito(True, "sess-hidden")
    context = SessionContext(
        session_id="sess-hidden",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        user_turns=[{"text": "secret", "corrective": False}],
    )
    first = service.jobs.handle_session_end(context, source_workspace=workspace)
    second = service.jobs.handle_session_end(context, source_workspace=workspace)
    assert first["captured"] is False
    assert second["captured"] is False
    assert service.store.read_record("sessions", "sess-hidden") is None
    assert service.store.list_records("jobs") == []


def test_digest_only_inbox_is_valid():
    settings = validate_settings({"cadence": {"digest": "only_inbox"}})
    assert settings["cadence"]["digest"] == "only_inbox"


def test_hundred_sessions_enqueue_without_duplicates(tmp_path, monkeypatch, home):
    service, project, _record = _install_and_project(tmp_path, monkeypatch, home)
    workspace = tmp_path / "project"
    for index in range(100):
        context = SessionContext(
            session_id=f"sess-bulk-{index}",
            host="dsh",
            project_root=str(project),
            workspace=str(workspace),
            events_path="",
            user_turns=[{"text": f"task {index}", "corrective": False}],
            signals={"achieved": True, "corrections": 0, "tool_loops": 0},
        )
        service.jobs.handle_session_end(context, source_workspace=workspace)
    assert len([s for s in service.store.list_records("sessions") if s["id"].startswith("sess-bulk-")]) == 100
    assert len([j for j in service.store.list_records("jobs") if j["session_id"].startswith("sess-bulk-")]) == 100
    duplicate = SessionContext(
        session_id="sess-bulk-0",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        user_turns=[{"text": "task 0", "corrective": False}],
    )
    service.jobs.handle_session_end(duplicate, source_workspace=workspace)
    assert len([j for j in service.store.list_records("jobs") if j["session_id"] == "sess-bulk-0"]) == 1


def test_reset_learning_keeps_sessions_and_audit(tmp_path, monkeypatch, home, fake_provider):
    service, project, _record = _install_and_project(tmp_path, monkeypatch, home)
    provider = service.providers.add(_provider_payload(fake_provider), "test-key")
    settings = service.settings_manager.load()
    settings["models"]["proposer"] = f"{provider['id']}/fake-model"
    service.settings_manager.save(settings)
    workspace = tmp_path / "project"
    context = SessionContext(
        session_id="sess-reset",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        user_turns=[{"text": "Fix it", "corrective": False}],
        signals={"achieved": False, "corrections": 1, "tool_loops": 0},
    )
    queued = service.jobs.handle_session_end(context, source_workspace=workspace)
    session = service.store.read_record("sessions", "sess-reset")
    outcome = service.jobs.analyzer.run(service.store.read_record("jobs", queued["job_id"]), session)
    assert outcome["state"] == "needs_apply"
    result = service.reset_learning()
    assert result["ok"] is True
    assert service.store.list_records("candidates") == []
    assert service.store.list_records("jobs") == []
    assert service.store.read_record("sessions", "sess-reset") is not None
    assert service.providers.public_providers()[0]["has_key"] is True


def test_manual_analysis_preview_includes_cost_estimate(tmp_path, monkeypatch, home, fake_provider):
    service, project, _record = _install_and_project(tmp_path, monkeypatch, home)
    provider = service.providers.add(_provider_payload(fake_provider), "test-key")
    settings = service.settings_manager.load()
    settings["models"]["proposer"] = f"{provider['id']}/fake-model"
    service.settings_manager.save(settings)
    workspace = tmp_path / "project"
    context = SessionContext(
        session_id="sess-preview",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        user_turns=[{"text": "Fix it", "corrective": False}],
        signals={"achieved": False, "corrections": 1, "tool_loops": 0},
    )
    service.jobs.handle_session_end(context, source_workspace=workspace)
    preview = service.session_analyze("sess-preview", confirm=False)
    assert preview["requires_confirmation"] is True
    assert "preview" in preview
    assert isinstance(preview.get("estimated_cost_usd"), float)


def test_local_api_serves_built_ui_with_csp(tmp_path, home):
    service = ConsumerService(home)
    server = LocalServer(service)
    server.start()
    try:
        req = urllib.request.Request(server.base_url + "/")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode()
            assert "<div id=\"root\">" in body
            assert "Content-Security-Policy" in resp.headers
            assert "script-src 'self'" in resp.headers["Content-Security-Policy"]
        req = urllib.request.Request(server.base_url + "/api/status")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=5)
        assert exc.value.code == 401
    finally:
        server.stop()


def test_job_runner_worker_end_to_end(tmp_path, monkeypatch, home, fake_provider):
    service, project, _record = _install_and_project(tmp_path, monkeypatch, home)
    provider = service.providers.add(_provider_payload(fake_provider), "test-key")
    settings = service.settings_manager.load()
    settings["models"]["proposer"] = f"{provider['id']}/fake-model"
    settings["models"]["composer"] = f"{provider['id']}/fake-model"
    settings["cadence"]["focus_mode"] = False
    settings["cadence"]["quiet_hours"] = None
    settings["cadence"]["weekly_cap"] = 10
    service.settings_manager.save(settings)
    workspace = tmp_path / "project"
    context = SessionContext(
        session_id="sess-worker",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        user_turns=[{"text": "Fix the build", "corrective": False}],
        signals={"achieved": False, "corrections": 2, "tool_loops": 0},
    )
    service.jobs.start()
    try:
        handled = service.jobs.handle_session_end(context, source_workspace=workspace)
        deadline = __import__("time").monotonic() + 15
        job = None
        while __import__("time").monotonic() < deadline:
            job = service.store.read_record("jobs", handled["job_id"])
            if job and job.get("state") in ("needs_apply", "applied", "noop", "failed"):
                break
            __import__("time").sleep(0.05)
        assert job is not None
        assert job.get("state") == "needs_apply", job
        assert job.get("candidate_id")
        candidate = service.candidates.get(job["candidate_id"])
        assert candidate["state"] == "needs_apply"
        assert candidate["safety"]["ok"] is True
    finally:
        service.jobs.stop()


def test_successful_session_is_noop_with_zero_cost(tmp_path, monkeypatch, home):
    service, project, _record = _install_and_project(tmp_path, monkeypatch, home)
    workspace = tmp_path / "project"
    context = SessionContext(
        session_id="sess-noop",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        user_turns=[{"text": "All good", "corrective": False}],
        signals={"achieved": True, "delivered": True, "corrections": 0, "tool_loops": 0},
    )
    result = service.jobs.handle_session_end(context, source_workspace=workspace)
    job = service.store.read_record("jobs", result["job_id"])
    session = service.store.read_record("sessions", "sess-noop")
    outcome = service.jobs.analyzer.run(job, session)
    assert outcome["state"] == "noop"
    assert outcome["cost_usd"] == 0.0
    assert service.budget.monthly_spent() == 0.0


def test_project_override_cannot_escalate_autopilot_policy():
    from morphd.consumer.settings import effective_settings

    global_settings = validate_settings(
        {
            "trust_level": "autopilot",
            "auto_apply": True,
            "autopilot_ack": True,
            "undo_days": 30,
            "budget": {"monthly_usd": 5, "per_session_usd": 0.5, "per_job_usd": 1.0},
        }
    )
    project = {
        "trust_level": "autopilot",
        "change_appetite": "bold",
        "auto_apply": True,
        "sensitive": False,
        "excluded_globs": [],
    }
    effective = effective_settings(global_settings, project)
    assert effective["change_appetite"] == "conservative"


def test_trial_neutral_result_reverts_end_to_end(tmp_path, monkeypatch, home, fake_provider):
    service, project, _record = _install_and_project(tmp_path, monkeypatch, home)
    provider = service.providers.add(_provider_payload(fake_provider), "test-key")
    settings = service.settings_manager.load()
    settings["models"]["proposer"] = f"{provider['id']}/fake-model"
    settings["trial_sessions"] = 1
    service.settings_manager.save(settings)
    workspace = tmp_path / "project"
    context = SessionContext(
        session_id="sess-trial",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        user_turns=[{"text": "Fix it", "corrective": False}],
        signals={"achieved": False, "corrections": 1, "tool_loops": 0},
    )
    queued = service.jobs.handle_session_end(context, source_workspace=workspace)
    session = service.store.read_record("sessions", "sess-trial")
    outcome = service.jobs.analyzer.run(service.store.read_record("jobs", queued["job_id"]), session)
    candidate = service.candidates.apply(outcome["candidate_id"], confirm=True)
    assert candidate["state"] == "applied"
    observed = dict(session)
    observed["id"] = "sess-trial-observed"
    observed["signals"] = {"achieved": False, "corrections": 1, "tool_loops": 0}
    decisions = service.candidates.observe_session(observed)
    assert decisions
    final = service.candidates.get(outcome["candidate_id"])
    assert final["state"] == "reverted"


def test_send_preview_lists_harness_not_workspace_source(tmp_path, monkeypatch, home):
    service, project, _record = _install_and_project(tmp_path, monkeypatch, home)
    workspace = tmp_path / "project"
    (workspace / "customer-data").mkdir(exist_ok=True)
    (workspace / "customer-data" / "secret.txt").write_text("do not send", encoding="utf-8")
    context = SessionContext(
        session_id="sess-preview-files",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        user_turns=[{"text": "Fix it", "corrective": False}],
        signals={"achieved": False, "corrections": 1, "tool_loops": 0},
    )
    service.jobs.handle_session_end(context, source_workspace=workspace)
    preview = service.privacy_preview("sess-preview-files")
    assert "AGENTS.md" in preview["files"]
    assert not any("secret.txt" in name for name in preview["files"])
    assert "not sent" in preview["note"] or "not sent" in preview.get("note", "")
