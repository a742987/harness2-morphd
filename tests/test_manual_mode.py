from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from morphd.consumer.api import LocalServer
from morphd.consumer.errors import ConsumerError
from morphd.consumer.service import ConsumerService


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MORPHD_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("MORPHD_VAULT_PASSPHRASE", "test-passphrase")
    monkeypatch.setattr("morphd.consumer.hosts.host_running", lambda kind: False)
    monkeypatch.setattr("morphd.consumer.keychain._keyring_available", lambda: False)
    for name in ("DSH_HOME", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "home"


def _snapshot(root: Path) -> dict[str, bytes]:
    root = Path(root)
    snapshot: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            snapshot[relative + "/"] = b""
        elif path.is_file():
            snapshot[relative] = path.read_bytes()
    return snapshot


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "selected-project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
    (project / "AGENTS.md").write_text("# Project guidance\n\n- Keep tests green.\n", encoding="utf-8")
    return project


def _config(tmp_path: Path) -> Path:
    config = tmp_path / "host-config"
    (config / "profiles").mkdir(parents=True)
    (config / "projects").mkdir(parents=True)
    (config / "profiles" / "settings.json").write_text('{"theme": "dark"}\n', encoding="utf-8")
    (config / "settings.json").write_text('{"theme": "dark"}\n', encoding="utf-8")
    return config


def _write_claude_session(
    config: Path,
    session_id: str,
    project: Path,
    *,
    mtime: float | None = None,
) -> Path:
    path = config / "projects" / "demo" / f"{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "type": "user",
            "session_id": session_id,
            "cwd": str(project),
            "timestamp": "2026-10-22T10:00:00Z",
            "message": {"content": "Fix the build"},
        },
        {
            "type": "assistant",
            "session_id": session_id,
            "timestamp": "2026-10-22T10:00:05Z",
            "message": {"content": [{"type": "text", "text": "Fixed the build."}]},
        },
        {
            "type": "result",
            "session_id": session_id,
            "subtype": "success",
            "is_error": False,
            "timestamp": "2026-10-22T10:00:10Z",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def _write_dsh_session(config: Path, session_id: str, project: Path) -> Path:
    path = config / "sessions" / session_id / "session.v1.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "type": "session",
            "version": 3,
            "id": session_id,
            "cwd": str(project),
            "createdAt": "2026-10-22T10:00:00Z",
        },
        {
            "type": "user/message",
            "data": {
                "message": {
                    "source": {"kind": "user"},
                    "content": [{"type": "text", "text": "Fix the build"}],
                }
            },
        },
        {"type": "turn/end", "data": {"reason": {"kind": "completed"}}},
    ]
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return path


def _write_codex_session(config: Path, session_id: str, project: Path) -> Path:
    path = config / "sessions" / "2026" / f"rollout-{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": str(project)},
        },
        {
            "type": "item",
            "session_id": session_id,
            "item": {"item_type": "user_message", "text": "Fix the build"},
        },
        {
            "type": "item",
            "session_id": session_id,
            "item": {"item_type": "agent_message", "text": "Fixed the build."},
        },
        {
            "type": "turn.completed",
            "session_id": session_id,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    ]
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    return path


def test_registration_is_read_only_and_not_an_adapted_host(home, tmp_path):
    project = _project(tmp_path)
    config = _config(tmp_path)
    service = ConsumerService(home)
    home_before = _snapshot(home)
    project_before = _snapshot(project)
    config_before = _snapshot(config)

    scope = service.manual_project_register(
        {
            "host": "claude-code",
            "project_root": str(project),
            "config_dir": str(config),
            "label": "Manual demo",
        }
    )

    assert scope["id"].startswith("manual_")
    assert scope["host"] == "claude-code"
    assert Path(scope["project_root"]) == project.resolve()
    assert Path(scope["config_dir"]) == config.resolve()
    assert scope["label"] == "Manual demo"
    assert scope["read_only"] is True
    assert scope["auto_trigger"] is False
    assert scope["created_at"]
    assert scope["updated_at"]

    home_after = _snapshot(home)
    assert set(home_after) - set(home_before) == {"manual-projects.json"}
    for path, content in home_before.items():
        assert home_after[path] == content
    assert _snapshot(project) == project_before
    assert _snapshot(config) == config_before

    document = json.loads((home / "manual-projects.json").read_text(encoding="utf-8"))
    assert document["version"] == 1
    assert [record["id"] for record in document["projects"]] == [scope["id"]]

    status = service.status()
    assert status["hosts"] == []
    assert status["connected_hosts"] == []
    assert service.hosts.install_records() == []
    assert [item["id"] for item in service.manual_projects_list()] == [scope["id"]]


def test_invalid_registration_writes_nothing(home, tmp_path):
    project = _project(tmp_path)
    _config(tmp_path)
    service = ConsumerService(home)
    before = _snapshot(tmp_path)
    invalid_payloads = [
        {"host": "unknown", "project_root": str(project)},
        {"host": "claude-code", "project_root": ""},
        {"host": "claude-code", "project_root": str(tmp_path / "missing-project")},
        {"host": "claude-code", "project_root": str(project / "AGENTS.md")},
        {
            "host": "claude-code",
            "project_root": str(project),
            "config_dir": str(tmp_path / "missing-config"),
        },
        {
            "host": "claude-code",
            "project_root": str(project),
            "config_dir": str(project / "AGENTS.md"),
        },
    ]
    for payload in invalid_payloads:
        with pytest.raises(ConsumerError):
            service.manual_project_register(payload)

    assert _snapshot(tmp_path) == before
    assert not (home / "manual-projects.json").exists()
    assert service.manual_projects_list() == []


def test_import_latest_session_without_job_or_model(home, tmp_path, monkeypatch):
    project = _project(tmp_path)
    config = _config(tmp_path)
    _write_claude_session(config, "sess-manual", project)
    service = ConsumerService(home)
    assert service.providers.public_providers() == []
    scope = service.manual_project_register(
        {"host": "claude-code", "project_root": str(project), "config_dir": str(config)}
    )
    project_before = _snapshot(project)
    config_before = _snapshot(config)

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("manual import must not enqueue a job or call a model")

    monkeypatch.setattr("morphd.consumer.providers.ProvidersManager.clients_for_role", _forbidden)
    monkeypatch.setattr("morphd.consumer.jobs.JobRunner.enqueue", _forbidden)
    monkeypatch.setattr("morphd.consumer.jobs.JobRunner.handle_session_end", _forbidden)

    result = service.manual_project_import(scope["id"])

    assert result["source"] == "manual-project"
    session = result["session"]
    assert session["id"] == "sess-manual"
    assert session["host"] == "claude-code"
    assert session["project"] == str(project.resolve())
    assert session["job_id"] is None

    record = service.store.read_record("sessions", "sess-manual")
    assert record is not None
    assert record["project"] == str(project.resolve())
    assert record["source_workspace"] == str(project.resolve())
    assert record["workspace"] == str((service.store.home / "sessions" / "sess-manual" / "workspace"))
    assert len(service.store.list_records("sessions")) == 1
    assert service.store.list_records("jobs") == []
    assert service.jobs.current_job is None
    assert _snapshot(project) == project_before
    assert _snapshot(config) == config_before


def test_import_picks_the_latest_session(home, tmp_path):
    project = _project(tmp_path)
    config = _config(tmp_path)
    now = time.time()
    _write_claude_session(config, "sess-old", project, mtime=now - 600)
    _write_claude_session(config, "sess-new", project, mtime=now)
    service = ConsumerService(home)
    scope = service.manual_project_register(
        {"host": "claude-code", "project_root": str(project), "config_dir": str(config)}
    )
    result = service.manual_project_import(scope["id"])
    assert result["session"]["id"] == "sess-new"
    assert len(service.store.list_records("sessions")) == 1


def test_import_uses_dsh_home_and_selected_project_root(home, tmp_path, monkeypatch):
    project = _project(tmp_path)
    dsh_config = tmp_path / "dsh-home"
    (dsh_config / "profiles").mkdir(parents=True)
    _write_dsh_session(dsh_config, "sess-dsh-manual", project)
    monkeypatch.setenv("DSH_HOME", str(dsh_config))
    service = ConsumerService(home)
    scope = service.manual_project_register({"host": "dsh", "project_root": str(project)})
    assert scope["config_dir"] is None

    result = service.manual_project_import(scope["id"])

    assert result["session"]["id"] == "sess-dsh-manual"
    assert result["session"]["project"] == str(project.resolve())
    record = service.store.read_record("sessions", "sess-dsh-manual")
    assert record["source_workspace"] == str(project.resolve())
    assert service.store.list_records("jobs") == []


def test_import_codex_session(home, tmp_path):
    project = _project(tmp_path)
    config = _config(tmp_path)
    _write_codex_session(config, "sess-codex-manual", project)
    service = ConsumerService(home)
    scope = service.manual_project_register(
        {"host": "codex", "project_root": str(project), "config_dir": str(config)}
    )
    result = service.manual_project_import(scope["id"])
    assert result["session"]["id"] == "sess-codex-manual"
    assert result["session"]["project"] == str(project.resolve())
    assert service.store.list_records("jobs") == []


def test_import_without_session_returns_clear_error(home, tmp_path):
    project = _project(tmp_path)
    config = _config(tmp_path)
    service = ConsumerService(home)
    scope = service.manual_project_register(
        {"host": "claude-code", "project_root": str(project), "config_dir": str(config)}
    )
    with pytest.raises(ConsumerError) as excinfo:
        service.manual_project_import(scope["id"])
    assert "没有找到" in excinfo.value.message
    assert "Claude Code" in excinfo.value.message
    assert service.store.list_records("sessions") == []
    assert service.store.list_records("jobs") == []

    # A config dir that later disappears is ignored gracefully, with the same clear error.
    shutil.rmtree(config)
    with pytest.raises(ConsumerError) as excinfo:
        service.manual_project_import(scope["id"])
    assert "没有找到" in excinfo.value.message


def test_delete_removes_only_the_scope_record(home, tmp_path):
    project = _project(tmp_path)
    config = _config(tmp_path)
    _write_claude_session(config, "sess-keep", project)
    service = ConsumerService(home)
    scope = service.manual_project_register(
        {"host": "claude-code", "project_root": str(project), "config_dir": str(config)}
    )
    service.manual_project_import(scope["id"])
    project_before = _snapshot(project)
    config_before = _snapshot(config)

    assert service.manual_project_delete(scope["id"]) == {"ok": True}
    assert service.manual_projects_list() == []
    document = json.loads((home / "manual-projects.json").read_text(encoding="utf-8"))
    assert document["projects"] == []
    assert service.store.read_record("sessions", "sess-keep") is not None
    assert _snapshot(project) == project_before
    assert _snapshot(config) == config_before
    with pytest.raises(ConsumerError):
        service.manual_project_import(scope["id"])


def test_registration_is_idempotent(home, tmp_path):
    project = _project(tmp_path)
    config = _config(tmp_path)
    service = ConsumerService(home)
    payload = {
        "host": "claude-code",
        "project_root": str(project),
        "config_dir": str(config),
        "label": "First label",
    }
    first = service.manual_project_register(payload)
    second = service.manual_project_register(payload)
    assert second["id"] == first["id"]
    assert second["created_at"] == first["created_at"]
    assert len(service.manual_projects_list()) == 1

    renamed = service.manual_project_register({**payload, "label": "Second label"})
    assert renamed["id"] == first["id"]
    assert renamed["label"] == "Second label"
    projects = service.manual_projects_list()
    assert len(projects) == 1
    assert projects[0]["id"] == first["id"]


class _ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.cookie = ""
        self.csrf = ""

    def request(self, method: str, path: str, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if self.csrf and method != "GET":
            headers["X-Morphd-CSRF"] = self.csrf
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read()
                if response.headers.get("Set-Cookie"):
                    self.cookie = response.headers["Set-Cookie"].split(";", 1)[0]
                return response.status, json.loads(body or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


def test_manual_projects_api(home, tmp_path):
    project = _project(tmp_path)
    config = _config(tmp_path)
    _write_claude_session(config, "sess-api", project)
    service = ConsumerService(home)
    server = LocalServer(service)
    server.start()
    client = _ApiClient(server.base_url)
    try:
        status, _ = client.request("GET", "/api/manual-projects")
        assert status == 401

        status, exchanged = client.request(
            "POST", "/api/auth/exchange", {"bootstrap_token": server.bootstrap_token}
        )
        assert status == 200
        client.csrf = exchanged["csrf_token"]

        status, scope = client.request(
            "POST",
            "/api/manual-projects",
            {"host": "claude-code", "project_root": str(project), "config_dir": str(config)},
        )
        assert status == 201, scope
        assert scope["read_only"] is True
        assert scope["auto_trigger"] is False

        status, listing = client.request("GET", "/api/manual-projects")
        assert status == 200
        assert [item["id"] for item in listing["projects"]] == [scope["id"]]

        status, status_payload = client.request("GET", "/api/status")
        assert status == 200
        assert status_payload["hosts"] == []

        status, imported = client.request(
            "POST", f"/api/manual-projects/{scope['id']}/import", {}
        )
        assert status == 200, imported
        assert imported["source"] == "manual-project"
        assert imported["session"]["project"] == str(project.resolve())
        assert service.store.list_records("jobs") == []

        status, deleted = client.request("DELETE", f"/api/manual-projects/{scope['id']}")
        assert status == 200
        assert deleted["ok"] is True
        assert service.manual_projects_list() == []
    finally:
        server.stop()


def test_unauthenticated_registration_is_rejected(home, tmp_path):
    project = _project(tmp_path)
    service = ConsumerService(home)
    server = LocalServer(service)
    server.start()
    client = _ApiClient(server.base_url)
    try:
        status, _ = client.request(
            "POST", "/api/manual-projects", {"host": "dsh", "project_root": str(project)}
        )
        assert status == 401
        assert service.manual_projects_list() == []
    finally:
        server.stop()
