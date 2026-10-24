from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from morphd.consumer import hosts as hosts_module
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
    monkeypatch.setattr("morphd.consumer.hosts.host_running", lambda kind: False)
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
    record = service.hosts_adapt({"candidate": candidate, "project_root": str(project)}, confirm=True)
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

    manager.add(_provider_payload(fake_provider, provider_id="p_other"), "other-key")
    assert len(manager.public_providers()) == 2
    removed = manager.delete_all()
    assert removed["deleted"] == 2
    assert removed["ok"] is True
    assert manager.public_providers() == []


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
    overview = manager.feedback_overview()
    assert overview["prompts"][0]["project_id"] == "p"
    assert overview["prompts"][0]["counts"]["ignore"] == 3
    stats = manager.respond_feedback("p", "intrusive")
    assert stats["ask_reason"] is False
    assert stats["quiet_until"]
    assert manager.feedback_overview()["prompts"] == []
    manager.pause("1d")
    assert manager.is_paused() is True
    manager.resume()
    assert manager.is_paused() is False


def test_host_discovery_adapt_uninstall(tmp_path, monkeypatch, home):
    service, project, record = _install_and_project(tmp_path, monkeypatch, home)
    assert record["status"] in ("adapted", "beta")
    assert Path(record["wrapper"]).is_file()
    assert service.hosts.record_for("dsh")["version"] == "0.2.1"
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    monkeypatch.setenv("PATH", str(empty_path))
    monkeypatch.setattr(hosts_module, "_common_bin_dirs", lambda: [])
    monkeypatch.setattr(hosts_module, "winreg", None)
    candidates = service.hosts_discover()
    assert any(c.get("installed") for c in candidates)
    result = service.host_uninstall("dsh", confirm=True)
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


def test_feedback_prompt_and_response_api(tmp_path, monkeypatch, home):
    service = ConsumerService(home)
    project = service.projects.for_root(tmp_path)
    for _ in range(3):
        service.attention.record_feedback(project["id"], "ignore")
    server = LocalServer(service)
    server.start()
    client = ApiClient(server.base_url)
    try:
        status, payload = client.request(
            "POST", "/api/auth/exchange", {"bootstrap_token": server.bootstrap_token}
        )
        assert status == 200
        client.csrf = payload["csrf_token"]

        status, payload = client.request("GET", "/api/status")
        assert status == 200
        prompts = payload["feedback"]["prompts"]
        assert [item["project_id"] for item in prompts] == [project["id"]]

        status, payload = client.request(
            "POST",
            "/api/feedback/respond",
            {"project_id": project["id"], "reason": "intrusive"},
        )
        assert status == 200, payload
        assert payload["attention"]["ask_reason"] is False
        assert payload["attention"]["quiet_until"]
        status, payload = client.request("GET", "/api/status")
        assert payload["feedback"]["prompts"] == []

        status, payload = client.request(
            "POST",
            "/api/feedback/respond",
            {"project_id": project["id"], "reason": "unknown"},
        )
        assert status == 400
        assert payload["error"]["code"] == "INVALID_REQUEST"
    finally:
        server.stop()


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
    result = service.reset_learning(confirm=True)
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


def test_duplicate_session_end_preserves_job_link(tmp_path, monkeypatch, home):
    service, project, _record = _install_and_project(tmp_path, monkeypatch, home)
    workspace = tmp_path / "project"
    context = SessionContext(
        session_id="sess-dedupe",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        user_turns=[{"text": "Fix the build", "corrective": False}],
        signals={"achieved": False, "corrections": 1, "tool_loops": 0},
    )
    first = service.jobs.handle_session_end(context, source_workspace=workspace)
    assert first["queued"] is True
    before = service.store.read_record("sessions", "sess-dedupe")
    second = service.jobs.handle_session_end(context, source_workspace=workspace)
    after = service.store.read_record("sessions", "sess-dedupe")
    assert second["deduplicated"] is True
    assert second["job_id"] == first["job_id"]
    assert after["job_id"] == before["job_id"] == first["job_id"]
    assert after["status"] == before["status"]
    assert len([j for j in service.store.list_records("jobs") if j["session_id"] == "sess-dedupe"]) == 1


def test_manual_analysis_cannot_bypass_pause_but_sensitive_project_is_manual_only(
    tmp_path, monkeypatch, home
):
    service, project, _record = _install_and_project(tmp_path, monkeypatch, home)
    workspace = tmp_path / "project"
    context = SessionContext(
        session_id="sess-manual-policy",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        user_turns=[{"text": "Fix it", "corrective": False}],
        signals={"achieved": False, "corrections": 1, "tool_loops": 0},
    )
    service.attention.pause("1d")
    blocked = service.jobs.handle_session_end(context, source_workspace=workspace, manual=True)
    assert blocked["queued"] is False
    assert blocked["reason"] == "paused"
    service.attention.resume()

    project_record = service.projects.for_root(workspace)
    service.project_put(project_record["id"], {"sensitive": True})
    sensitive = SessionContext(
        session_id="sess-sensitive",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        user_turns=[{"text": "Fix it", "corrective": False}],
        signals={"achieved": False, "corrections": 1, "tool_loops": 0},
    )
    auto = service.jobs.handle_session_end(sensitive, source_workspace=workspace)
    assert auto["queued"] is False
    assert auto["reason"] == "sensitive_project"
    manual = service.jobs.handle_session_end(sensitive, source_workspace=workspace, manual=True)
    assert manual["queued"] is True


def test_project_budget_cannot_exceed_global_hard_cap():
    from morphd.consumer.settings import effective_settings

    global_settings = validate_settings({})
    effective = effective_settings(
        global_settings,
        {
            "budget": {"monthly_usd": 99, "per_session_usd": 99, "per_job_usd": 99},
            "excluded_globs": [],
        },
    )
    assert effective["budget"]["monthly_usd"] == global_settings["budget"]["monthly_usd"]
    assert effective["budget"]["per_session_usd"] == global_settings["budget"]["per_session_usd"]
    assert effective["budget"]["per_job_usd"] == global_settings["budget"]["per_job_usd"]


def test_claude_harness_tree_never_includes_settings_or_hooks(tmp_path, monkeypatch, home):
    service, project, _record = _install_and_project(tmp_path, monkeypatch, home)
    # Register a fake Claude record and project surface without launching the host.
    claude_home = tmp_path / "claude-home"
    (claude_home).mkdir()
    (project / ".claude" / "skills" / "demo").mkdir(parents=True)
    (project / ".claude" / "skills" / "demo" / "SKILL.md").write_text("demo\n", encoding="utf-8")
    (project / ".claude" / "settings.local.json").write_text('{"hooks": {"Stop": []}}\n', encoding="utf-8")
    (project / "CLAUDE.md").write_text("# Claude\n", encoding="utf-8")
    record = {
        "host": "claude-code",
        "config_dir": str(claude_home),
        "project_root": str(project),
        "status": "beta",
        "integration": "wrapper",
        "wrapper": str(tmp_path / "claude.cmd"),
    }
    service.hosts._save_install(
        {"version": 1, "app_version": "0.5.0", "platform": "win32-x64", "hosts": [record]}
    )
    tree = service.hosts.read_harness_tree("claude-code", project_root=str(project))
    assert "CLAUDE.md" in tree
    assert ".claude/skills/demo/SKILL.md" in tree
    assert ".claude/settings.local.json" not in tree


def test_provider_public_payload_never_leaks_legacy_plaintext(tmp_path, monkeypatch, home):
    from morphd.consumer.providers import ProvidersManager

    store = ConsumerStore(home)
    store.write_json(
        store.providers_path,
        {
            "version": 1,
            "providers": [
                {
                    "id": "p_legacy",
                    "label": "Legacy",
                    "protocol": "openai-compatible",
                    "base_url": "https://example.com/v1",
                    "model": "m",
                    "key_ref": "vault:provider:p_legacy",
                    "api_key": "sk-should-not-leak",
                    "authorization": "Bearer secret",
                }
            ],
        },
    )
    manager = ProvidersManager(store)
    public = manager.public_providers()
    serialized = json.dumps(public)
    assert "sk-should-not-leak" not in serialized
    assert "Bearer secret" not in serialized


def test_api_lists_are_arrays_and_dangerous_writes_require_confirmation(
    tmp_path, monkeypatch, home
):
    service = ConsumerService(home)
    service.projects.for_root(tmp_path)
    server = LocalServer(service)
    server.start()
    client = ApiClient(server.base_url)
    try:
        status, payload = client.request(
            "POST", "/api/auth/exchange", {"bootstrap_token": server.bootstrap_token}
        )
        assert status == 200
        client.csrf = payload["csrf_token"]
        for path in ("/api/hosts", "/api/candidates", "/api/projects", "/api/providers"):
            status, payload = client.request("GET", path)
            assert status == 200, (path, payload)
            assert isinstance(payload, list), (path, type(payload))
        status, payload = client.request("POST", "/api/diagnostics/reset-learning", {})
        assert status == 400
        assert payload["error"]["code"] == "INVALID_REQUEST"
        status, payload = client.request(
            "POST", "/api/privacy/delete", {"scope": "all"}
        )
        assert status == 400
        status, payload = client.request("GET", "/api/exports/does-not-exist.json")
        assert status == 404
        assert payload["error"]["code"] in ("NOT_FOUND", "AUTH_REQUIRED") or status == 404
    finally:
        server.stop()


def test_api_apply_and_revert_require_confirm(tmp_path, monkeypatch, home, fake_provider):
    service, project, _record = _install_and_project(tmp_path, monkeypatch, home)
    provider = service.providers.add(_provider_payload(fake_provider), "test-key")
    settings = service.settings_manager.load()
    settings["models"]["proposer"] = f"{provider['id']}/fake-model"
    settings["models"]["composer"] = f"{provider['id']}/fake-model"
    service.settings_manager.save(settings)
    workspace = tmp_path / "project"
    context = SessionContext(
        session_id="sess-api-confirm",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        user_turns=[{"text": "Fix the build", "corrective": False}],
        signals={"achieved": False, "corrections": 1, "tool_loops": 0},
    )
    queued = service.jobs.handle_session_end(context, source_workspace=workspace)
    session = service.store.read_record("sessions", "sess-api-confirm")
    outcome = service.jobs.analyzer.run(
        service.store.read_record("jobs", queued["job_id"]), session
    )
    assert outcome["state"] == "needs_apply"
    candidate_id = outcome["candidate_id"]
    before = (project / "AGENTS.md").read_text(encoding="utf-8")
    server = LocalServer(service)
    server.start()
    client = ApiClient(server.base_url)
    try:
        status, payload = client.request(
            "POST", "/api/auth/exchange", {"bootstrap_token": server.bootstrap_token}
        )
        assert status == 200
        client.csrf = payload["csrf_token"]
        status, payload = client.request("POST", f"/api/candidates/{candidate_id}/apply", {})
        assert status == 400, payload
        assert (project / "AGENTS.md").read_text(encoding="utf-8") == before
        status, payload = client.request(
            "POST", f"/api/candidates/{candidate_id}/apply", {"confirm": True}
        )
        assert status == 200, payload
        assert payload["state"] == "applied"
        status, payload = client.request("POST", f"/api/candidates/{candidate_id}/revert", {})
        assert status == 400
        status, payload = client.request(
            "POST", f"/api/candidates/{candidate_id}/revert", {"confirm": True}
        )
        assert status == 200, payload
        assert payload["state"] == "reverted"
        assert (project / "AGENTS.md").read_text(encoding="utf-8") == before
    finally:
        server.stop()


def test_null_completion_signal_is_analyzed_not_noop(tmp_path, monkeypatch, home, fake_provider):
    service, project, _record = _install_and_project(tmp_path, monkeypatch, home)
    provider = service.providers.add(_provider_payload(fake_provider), "test-key")
    settings = service.settings_manager.load()
    settings["models"]["proposer"] = f"{provider['id']}/fake-model"
    service.settings_manager.save(settings)
    workspace = tmp_path / "project"
    context = SessionContext(
        session_id="sess-null",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        user_turns=[{"text": "Fix it", "corrective": False}],
        signals={"achieved": None, "delivered": None, "corrections": 0, "tool_loops": 0},
    )
    queued = service.jobs.handle_session_end(context, source_workspace=workspace)
    session = service.store.read_record("sessions", "sess-null")
    outcome = service.jobs.analyzer.run(service.store.read_record("jobs", queued["job_id"]), session)
    # C.4.4: an unknown (null) completion signal is a signal, not a smooth-session noop.
    assert outcome["state"] == "needs_apply", outcome


def test_auto_apply_requires_independent_baseline(tmp_path, monkeypatch, home, fake_provider):
    service, project, _record = _install_and_project(tmp_path, monkeypatch, home)
    provider = service.providers.add(_provider_payload(fake_provider), "test-key")
    settings = service.settings_manager.load()
    settings.update(
        {
            "trust_level": "autopilot",
            "auto_apply": True,
            "autopilot_ack": True,
            "undo_days": 30,
            "budget": {
                "monthly_usd": 5,
                "per_session_usd": 0.5,
                "per_job_usd": 1.0,
                "alert_ratio": 0.8,
                "confirm_before_analysis": False,
            },
        }
    )
    settings["models"]["proposer"] = f"{provider['id']}/fake-model"
    settings["models"]["composer"] = f"{provider['id']}/fake-model"
    service.settings_manager.save(settings)
    workspace = tmp_path / "project"

    def ctx(sid, start, end):
        return SessionContext(
            session_id=sid,
            host="dsh",
            project_root=str(project),
            workspace=str(workspace),
            events_path="",
            started_at=start,
            ended_at=end,
            end_reason="error",
            user_turns=[{"text": "Fix it", "corrective": False}],
            signals={"achieved": False, "corrections": 1, "tool_loops": 0},
        )

    # First failing session has no independent baseline → C.4.4 must not auto-apply.
    q1 = service.jobs.handle_session_end(ctx("sess-auto-1", "2026-10-22T10:00:00+00:00", "2026-10-22T10:05:00+00:00"), source_workspace=workspace)
    s1 = service.store.read_record("sessions", "sess-auto-1")
    o1 = service.jobs.analyzer.run(service.store.read_record("jobs", q1["job_id"]), s1)
    assert o1["state"] == "needs_apply", o1

    # The second failing session now has the first as a baseline → auto-apply is allowed.
    q2 = service.jobs.handle_session_end(ctx("sess-auto-2", "2026-10-22T11:00:00+00:00", "2026-10-22T11:05:00+00:00"), source_workspace=workspace)
    s2 = service.store.read_record("sessions", "sess-auto-2")
    o2 = service.jobs.analyzer.run(service.store.read_record("jobs", q2["job_id"]), s2)
    assert o2["state"] == "applied", o2


def test_apply_reports_conflict_when_target_hand_edited(tmp_path, monkeypatch, home, fake_provider):
    service, project, _record = _install_and_project(tmp_path, monkeypatch, home)
    provider = service.providers.add(_provider_payload(fake_provider), "test-key")
    settings = service.settings_manager.load()
    settings["models"]["proposer"] = f"{provider['id']}/fake-model"
    settings["models"]["composer"] = f"{provider['id']}/fake-model"
    service.settings_manager.save(settings)
    workspace = tmp_path / "project"
    context = SessionContext(
        session_id="sess-conflict",
        host="dsh",
        project_root=str(project),
        workspace=str(workspace),
        events_path="",
        user_turns=[{"text": "Fix it", "corrective": False}],
        signals={"achieved": False, "corrections": 1, "tool_loops": 0},
    )
    queued = service.jobs.handle_session_end(context, source_workspace=workspace)
    session = service.store.read_record("sessions", "sess-conflict")
    outcome = service.jobs.analyzer.run(service.store.read_record("jobs", queued["job_id"]), session)
    cid = outcome["candidate_id"]
    cand = service.candidates.get(cid)
    assert "AGENTS.md" in cand["files_changed"]
    target = service.hosts.target_for(cand["host"], "AGENTS.md", project_root=cand["project"])
    target.write_text("# Manually rewritten by the user\n", encoding="utf-8")
    with pytest.raises(ConsumerError) as exc:
        service.candidates.apply(cid, confirm=True)
    # C.4.3/C.4.4: a manual edit of a target file is APPLY_CONFLICT, and the candidate is kept.
    assert exc.value.code == "APPLY_CONFLICT"
    assert service.candidates.get(cid)["state"] == "needs_apply"


def test_baseline_excludes_paused_sessions(home):
    from morphd.consumer.candidates import CandidateManager

    store = ConsumerStore(home)
    mgr = CandidateManager(store)
    ended = "2026-10-22T10:00:00+00:00"

    def sess(sid, status):
        return {
            "version": 1,
            "id": sid,
            "host": "dsh",
            "project_id": "proj_x",
            "ended_at": ended,
            "status": status,
            "signals": {"achieved": True, "corrections": 0, "tool_loops": 0, "turns": 2},
        }

    store.write_record("sessions", "s_ok", sess("s_ok", "recorded"))
    store.write_record("sessions", "s_paused", sess("s_paused", "paused"))
    store.write_record("sessions", "s_vacation", sess("s_vacation", "vacation"))
    base = mgr.baseline({"host": "dsh", "project_id": "proj_x", "applied_at": "2026-10-23T00:00:00+00:00"})
    # C.4.4: baseline uses only non-paused, non-vacation, non-incognito sessions.
    assert base["n"] == 1
    assert base["session_ids"] == ["s_ok"]


def test_settings_validation_cadence_bools_and_model_refs():
    with pytest.raises(Exception):
        validate_settings({"cadence": {"focus_mode": "yes"}})
    with pytest.raises(Exception):
        validate_settings({"auto_rollback": "true"})
    with pytest.raises(Exception):
        validate_settings({"models": {"proposer": "just-a-model"}})
    with pytest.raises(Exception):
        validate_settings({"models": {"fallbacks": ["no-slash"]}})
    ok = validate_settings({"models": {"proposer": "p_test/fake-model", "fallbacks": ["p_b/model-b"]}})
    assert ok["models"]["proposer"] == "p_test/fake-model"


def test_project_budget_and_cadence_validation():
    from morphd.consumer.settings import validate_project

    validate_project(
        {
            "root": "/x",
            "budget": {"monthly_usd": 5, "per_session_usd": 0.5},
            "cadence": {"weekly_cap": 2, "quiet_hours": ["22:00", "08:00"]},
        }
    )
    with pytest.raises(Exception):
        validate_project({"root": "/x", "budget": {"monthly_usd": 1, "per_session_usd": 9}})
    with pytest.raises(Exception):
        validate_project({"root": "/x", "cadence": {"weekly_cap": 99}})
