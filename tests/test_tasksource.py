from __future__ import annotations

import json
from pathlib import Path

from conftest import write_session

from morphd import contract, tasksource


def _events(cwd: Path, *, fail: bool = True, healthy: bool = False) -> list[dict]:
    seq = 0
    rows = []

    def add(kind, data):
        nonlocal seq
        rows.append({"type": kind, "seq": seq, "time": seq, "data": data})
        seq += 1

    add("turn/start", {"turn": 1})
    add(
        "user/message",
        {
            "role": "user",
            "id": "u1",
            "content": [{"type": "text", "text": "Build the report."}],
            "source": {"kind": "user"},
        },
    )
    for i in range(3 if not healthy else 0):
        add("tool/call", {"turn": 1, "step": 1, "callId": f"c{i}", "name": "bash", "arguments": "{}"})
        add(
            "tool/result",
            {
                "turn": 1,
                "step": 1,
                "message": {
                    "role": "tool",
                    "id": f"r{i}",
                    "toolCallId": f"c{i}",
                    "isError": True,
                    "content": [{"type": "text", "text": "same"}],
                },
                "error": {"name": "E", "code": "SAME"},
            },
        )
    for i in range(3 if not healthy else 0):
        add("approval/decided", {"id": f"a{i}", "outcome": "rejected"})
    add("step/end", {"turn": 1, "step": 1})
    add(
        "turn/end",
        {
            "turn": 1,
            "reason": {"kind": "error", "error": {"message": "boom", "code": "E"}}
            if fail
            else {"kind": "completed"},
        },
    )
    if not healthy:
        add("turn/start", {"turn": 2})
        add(
            "user/message",
            {
                "role": "user",
                "id": "u2",
                "content": [{"type": "text", "text": "No, use the other file instead."}],
                "source": {"kind": "user"},
            },
        )
        add("turn/end", {"turn": 2, "reason": {"kind": "completed"}})
    if healthy:
        return [row for row in rows if row["type"] not in {"tool/call", "tool/result", "approval/decided"}]
    return rows


def test_read_compressed_session(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    path = tmp_path / "sessions" / "--project--" / "abc" / "session.v3.jsonl.zstd"
    write_session(path, sid="session-1", cwd=cwd, events=_events(cwd))
    finding = tasksource.read_session(path)
    assert finding.session_id == "session-1"
    assert "failed-turn" in finding.failures
    assert "approval-rejected" in finding.failures
    assert "tool-error-loop" in finding.failures
    assert "user-correction" in finding.failures
    assert len(finding.turns) == 2
    assert finding.turns[1].corrective
    assert finding.last_reason == "completed"


def test_failure_rule_thresholds(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    path = tmp_path / "sessions" / "--project--" / "abc" / "session.v3.jsonl"
    write_session(path, sid="threshold", cwd=cwd, events=_events(cwd), compressed=False)
    loose = tasksource.read_session(
        path,
        rules=tasksource.FailureRules(
            min_approval_rejects=4, min_tool_loops=2, include_corrections=False
        ),
    )
    assert "approval-rejected" not in loose.failures
    assert "tool-error-loop" not in loose.failures
    assert "user-correction" not in loose.failures
    assert "failed-turn" in loose.failures


def test_read_raw_session(tmp_path):
    cwd = tmp_path / "project"
    cwd.mkdir()
    path = tmp_path / "sessions" / "--project--" / "abc" / "session.v3.jsonl"
    write_session(path, sid="session-raw", cwd=cwd, events=_events(cwd), compressed=False)
    assert tasksource.read_session(path).session_id == "session-raw"


def test_import_sessions_creates_sanitized_task(tmp_path, env, workspace):
    (workspace / ".env").write_text("DEEPSEEK_API_KEY=" + "sk-" + "abcdefghijklmnop" + "\n", encoding="utf-8")
    sessions = tmp_path / "sessions"
    session_path = sessions / "--project--" / "sid-1" / "session.v3.jsonl.zstd"
    write_session(session_path, sid="sid-1", cwd=workspace, events=_events(workspace))
    criteria = tmp_path / "criteria"
    criteria.mkdir()
    (criteria / "sid-1.json").write_text(
        json.dumps(
            {
                "checks": [{"type": "file_exists", "path": "out.md"}],
                "expected_deliverables": ["out.md"],
            }
        )
    )
    store_ = contract.TaskStore(env["settings"].tasks_root)
    result = tasksource.import_sessions(
        sessions_root=sessions,
        task_store=store_,
        domain="demo",
        workspace=workspace,
        criteria_dir=criteria,
        split=True,
        holdout=0.2,
        test=0.5,
    )
    assert len(result.imported) == 1
    ref = result.imported[0]
    task = store_.get(ref)
    assert task.source_runtime == "dsh"
    assert task.user_turns[0].text == "Build the report."
    assert task.checks[0]["type"] == "file_exists"
    workspace_out = store_.workspace_dir(ref)
    assert not (workspace_out / ".env").exists()
    assert store_.verify_workspace(ref)
    assert task.provenance["session_id"] == "sid-1"
    assert result.split_path and result.split_path.is_file()


def test_import_skips_healthy_sessions(tmp_path, env):
    cwd = tmp_path / "project"
    cwd.mkdir()
    sessions = tmp_path / "sessions"
    path = sessions / "--project--" / "ok" / "session.v3.jsonl"
    write_session(
        path, sid="ok", cwd=cwd, events=_events(cwd, fail=False, healthy=True), compressed=False
    )
    store_ = contract.TaskStore(env["settings"].tasks_root)
    result = tasksource.import_sessions(
        sessions_root=sessions, task_store=store_, domain="demo"
    )
    assert result.imported == []
    assert result.skipped[0]["reason"] == "no failure signal"
