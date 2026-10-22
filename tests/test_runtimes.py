from __future__ import annotations

import json
from pathlib import Path

import pytest

from morphd import contract, runtimes
from morphd.budget import BudgetGuard, Limits
from morphd.contract import UserTurn
from morphd.runtimes.base import RuntimeError_


def _adapter(env, runtime: str, guard=None):
    return runtimes.build_adapter(
        runtime,
        tasks=contract.TaskStore(env["settings"].tasks_root),
        settings=env["settings"],
        price=env["price"],
        guard=guard or BudgetGuard(limits=Limits(usd=10, calls=100)),
        model="deepseek-flash" if runtime == "dsh" else "fake-model",
    )


def _replay_task(add_task):
    turns = [
        UserTurn("FAIL_FIRST: write hello.txt"),
        UserTurn("The command failed; please finish it.", corrective=True),
    ]
    return add_task("replay-1", instruction=turns[0].text, turns=turns)


@pytest.mark.parametrize("runtime", ["dsh", "codex", "claude-code"])
def test_one_turn_rollout(env, add_task, runtime):
    ref = add_task("demo-1")
    adapter = _adapter(env, runtime)
    detail = adapter.preflight()
    assert detail["version"]
    run_dir = adapter.execute(ref, None, f"{runtime}-run-1")
    meta = json.loads((run_dir / "meta.json").read_text())
    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert meta["status"] == "done"
    assert metrics["achieved"] is True
    assert metrics["delivered"] is True
    assert metrics["convergence_turns"] == 1
    assert metrics["usage_present"]
    assert adapter.delivered(run_dir)
    assert "hello.txt" in adapter.deliverable_files(run_dir)
    assert adapter.score(run_dir, ref)["all_pass"] is True
    assert "hello" in adapter.deliverable_text(run_dir)
    assert adapter.trajectory(run_dir, obs_trunc=10_000)
    assert (run_dir / ".morphd-complete").is_file()
    if runtime == "dsh":
        profile = Path(meta["prepare_extras"]["profile_path"])
        package = json.loads((profile / "package.json").read_text())
        assert package["dsh"]["profile"]["bundles"] == [
            "@deepseek-ai/dsh-base",
            "@deepseek-ai/dsh-headless",
        ]
        patch = (profile / "cordis.patch.yml").read_text()
        assert "workspace-write" in patch and "policy: ask" in patch
        assert "morphd-approver.mjs" in patch
        assert "sandbox: danger-full-access" not in patch
        assert "approval: never" not in patch
        assert not (Path(meta["runtime_home"]) / ".credentials.yaml").exists()


@pytest.mark.parametrize("runtime", ["dsh", "codex", "claude-code"])
def test_replay_uses_correction_turn(env, add_task, runtime):
    ref = _replay_task(add_task)
    adapter = _adapter(env, runtime)
    adapter.preflight()
    run_dir = adapter.execute(ref, None, f"{runtime}-replay")
    replay = json.loads((run_dir / "replay.json").read_text())
    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert replay["injected"][0]["achieved"] is False
    assert replay["injected"][1]["achieved"] is True
    assert replay["convergence_turns"] == 2
    assert replay["correction_messages"] == 1
    assert metrics["achieved"] is True
    assert metrics["delivered"] is True


def test_cross_runtime_workspace_hash_matches_contract(env, add_task):
    ref = add_task("cross-runtime")
    task = contract.TaskStore(env["settings"].tasks_root).get(ref)
    hashes = {}
    for runtime in ("dsh", "codex", "claude-code"):
        adapter = _adapter(env, runtime)
        adapter.preflight()
        run_dir = adapter.execute(ref, None, f"{runtime}-cross")
        meta = json.loads((run_dir / "meta.json").read_text())
        hashes[runtime] = meta["workspace_sha256"]
    assert set(hashes.values()) == {task.workspace_sha256}


def test_idempotent_execute_does_not_spend(env, add_task):
    ref = add_task("demo-idem")
    guard = BudgetGuard(limits=Limits(usd=10, calls=100))
    adapter = _adapter(env, "dsh", guard)
    adapter.preflight()
    first = adapter.execute(ref, None, "same-run")
    calls = guard.calls
    second = adapter.execute(ref, None, "same-run")
    assert first == second
    assert guard.calls == calls


def test_dsh_tool_error_loop(env, add_task, monkeypatch):
    monkeypatch.setenv("FAKE_TOOL_LOOP", "1")
    ref = add_task("loop-1")
    adapter = _adapter(env, "dsh")
    adapter.preflight()
    run_dir = adapter.execute(ref, None, "loop-run")
    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert metrics["tool_error_loop"] is True


def test_dsh_approvals_are_audited(env, add_task, monkeypatch):
    monkeypatch.setenv("FAKE_APPROVALS", "1")
    ref = add_task("approved-1")
    adapter = _adapter(env, "dsh")
    adapter.preflight()
    run_dir = adapter.execute(ref, None, "approval-run")
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["approvals"]["n"] == 2
    assert meta["approvals"]["allow_rate"] == 0.5
    lines = (run_dir / "approvals.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2


def test_claude_unknown_schema_fails_closed(env, add_task, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_UNKNOWN", "1")
    ref = add_task("unknown-1")
    adapter = _adapter(env, "claude-code")
    adapter.preflight()
    with pytest.raises(RuntimeError_, match="unknown Claude Code"):
        adapter.execute(ref, None, "unknown-run")
    run_dir = env["settings"].runtime_runs_root("claude-code") / "rollouts" / "unknown-1" / "unknown-run"
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["status"] == "failed"
    assert not (run_dir / ".morphd-complete").exists()


def test_unknown_turn_reason_is_n_a_not_failure(env, add_task, monkeypatch):
    monkeypatch.setenv("FAKE_DSH_UNKNOWN", "1")
    ref = add_task("unknown-reason")
    adapter = _adapter(env, "dsh")
    adapter.preflight()
    run_dir = adapter.execute(ref, None, "unknown-reason-run")
    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert metrics["achieved"] is None
    assert metrics["bucket"] == "other"


def test_codex_budget_stops_before_next_turn(env, add_task):
    turns = [
        UserTurn("FAIL_FIRST: one"),
        UserTurn("two", corrective=True),
        UserTurn("three", corrective=True),
    ]
    ref = add_task("budget-1", instruction=turns[0].text, turns=turns)
    guard = BudgetGuard(limits=Limits(usd=100, calls=1))
    adapter = _adapter(env, "codex", guard)
    adapter.preflight()
    from morphd.budget import BudgetExceeded

    with pytest.raises(BudgetExceeded):
        adapter.execute(ref, None, "budget-run")
    assert guard.calls == 1


def test_runtime_config_from_task_workspace_is_removed(env, add_task, tmp_path):
    claude_ws = tmp_path / "claude-ws"
    claude_ws.mkdir()
    (claude_ws / "README.md").write_text("task")
    (claude_ws / ".claude").mkdir()
    (claude_ws / ".claude" / "settings.json").write_text(
        '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "touch pwned"}]}]}}'
    )
    (claude_ws / ".mcp.json").write_text("{}")
    add_task("claude-config", ws=claude_ws)
    adapter = _adapter(env, "claude-code")
    adapter.preflight()
    run_dir = adapter.execute("claude-config", None, "claude-config-run")
    settings = json.loads((run_dir / "workspace" / ".claude" / "settings.json").read_text())
    assert settings == {"permissions": {"deny": ["WebFetch", "WebSearch"]}}
    assert not (run_dir / "workspace" / ".mcp.json").exists()

    codex_ws = tmp_path / "codex-ws"
    codex_ws.mkdir()
    (codex_ws / "README.md").write_text("task")
    (codex_ws / ".codex").mkdir()
    (codex_ws / ".codex" / "hooks.json").write_text("{}")
    add_task("codex-config", ws=codex_ws)
    codex = _adapter(env, "codex")
    codex.preflight()
    run_dir = codex.execute("codex-config", None, "codex-config-run")
    assert not (run_dir / "workspace" / ".codex" / "hooks.json").exists()

    dsh_ws = tmp_path / "dsh-ws"
    dsh_ws.mkdir()
    (dsh_ws / "README.md").write_text("task")
    (dsh_ws / ".dsh" / "skills" / "evil").mkdir(parents=True)
    (dsh_ws / ".dsh" / "skills" / "evil" / "SKILL.md").write_text("evil")
    add_task("dsh-config", ws=dsh_ws)
    dsh = _adapter(env, "dsh")
    dsh.preflight()
    run_dir = dsh.execute("dsh-config", None, "dsh-config-run")
    assert not (run_dir / "workspace" / ".dsh").exists()


def test_secret_leak_fails_the_run_and_keeps_security_status(env, add_task, monkeypatch):
    from morphd.security import SecurityError

    monkeypatch.setenv("FAKE_DSH_SECRET", "1")
    ref = add_task("leak-1")
    adapter = _adapter(env, "dsh")
    adapter.preflight()
    with pytest.raises(SecurityError):
        adapter.execute(ref, None, "leak-run")
    run_dir = env["settings"].runs_root / "dsh" / "rollouts" / "leak-1" / "leak-run"
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["status"] == "security-failed"
    assert meta["security"]["secrets"]
    assert not (run_dir / ".morphd-complete").exists()


def test_tampered_workspace_is_rejected(env, add_task):
    ref = add_task("tamper-1")
    workspace = contract.TaskStore(env["settings"].tasks_root).workspace_dir(ref)
    (workspace / "README.md").write_text("tampered")
    adapter = _adapter(env, "dsh")
    adapter.preflight()
    with pytest.raises(ValueError, match="workspace hash mismatch"):
        adapter.execute(ref, None, "tamper-run")


def test_failed_rollout_is_still_recorded_as_not_achieved(env, add_task, monkeypatch):
    monkeypatch.setenv("FAKE_DSH_REQUIRE_MARKER", "1")
    ref = add_task("base-fails")
    adapter = _adapter(env, "dsh")
    adapter.preflight()
    run_dir = adapter.execute(ref, None, "fail-run")
    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert metrics["achieved"] is False
    assert metrics["delivered"] is False
    assert (run_dir / ".morphd-complete").is_file()
