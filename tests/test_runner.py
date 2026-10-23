from __future__ import annotations

import json

import pytest

from morphd import _upstream, runner
from morphd.budget import BudgetExceeded


class _DummyProposer:
    spec = None
    audit_dir = None
    attempts = 1
    project = ""
    region = ""


def _options(**overrides):
    options = runner.RunOptions(
        runtimes=["dsh"],
        k=1,
        modes=("parallel",),
        split="test",
        budget_usd={"dsh": 10.0},
        budget_calls={"dsh": 100},
        proposer_channel="claude_cli",
        proposer_model="fake-proposer",
        models={"dsh": "deepseek-flash"},
    )
    for key, value in overrides.items():
        setattr(options, key, value)
    return options


def test_run_orchestration_writes_evidence(env, add_task, monkeypatch, capsys):
    add_task("demo-1", split="test")
    schedule = _upstream.core("schedule")

    def fake_run_sweep(k, *, adapter, prop, runs_root, library_root, split, **kwargs):
        tasks = adapter.tasks(split)
        for task in tasks:
            adapter.execute(task, None, f"{adapter.runtime}-sweep-{task}")
        return {
            "domains": [{"domain": "demo", "done": len(tasks), "failed": 0, "tasks": []}],
            "modes": ["agg"],
            "base": [],
            "selections": {},
        }

    monkeypatch.setattr(runner, "_check_proposer_ready", lambda channel: None)
    monkeypatch.setattr(runner, "_check_proposer_ready", lambda channel: None)
    monkeypatch.setattr(schedule, "run_sweep", fake_run_sweep)
    monkeypatch.setattr(runner, "_make_proposer", lambda *a, **k: _DummyProposer())
    result = runner.execute(_options(), env["settings"])
    captured = capsys.readouterr().out
    assert result["status"] == "done", captured
    record_path = next(
        (env["settings"].runs_root / "dsh" / "adapt" / "morphd-runs").glob("*.json")
    )
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["status"] == "done"
    assert record["preflight"]["runtime"] == "dsh"
    assert record["budget"]["calls"] == 1
    ns = record["namespace"]
    summary_path = env["settings"].runs_root / "dsh" / "adapt" / ns / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["status"] == "done"
    assert summary["morphd"]["runtime"] == "dsh"


def test_resume_rejects_model_change(env, add_task, monkeypatch):
    add_task("demo-1", split="test")
    schedule = _upstream.core("schedule")

    def fake_run_sweep(k, *, adapter, prop, runs_root, library_root, split, **kwargs):
        return {"domains": [{"domain": "demo", "done": 1, "failed": 0}], "modes": ["agg"]}

    monkeypatch.setattr(runner, "_check_proposer_ready", lambda channel: None)
    monkeypatch.setattr(runner, "_make_proposer", lambda *a, **k: _DummyProposer())
    monkeypatch.setattr(schedule, "run_sweep", fake_run_sweep)
    runner.execute(_options(), env["settings"])
    with pytest.raises(runner.RunError, match="controlled variables"):
        runner.execute(_options(models={"dsh": "other-model"}), env["settings"])


def test_run_budget_interruption_is_reported(env, add_task, monkeypatch):
    add_task("demo-1", split="test")
    schedule = _upstream.core("schedule")

    def fake_run_sweep(*args, **kwargs):
        raise BudgetExceeded("call limit", {"calls": 1})

    monkeypatch.setattr(runner, "_check_proposer_ready", lambda channel: None)
    monkeypatch.setattr(runner, "_check_proposer_ready", lambda channel: None)
    monkeypatch.setattr(schedule, "run_sweep", fake_run_sweep)
    monkeypatch.setattr(runner, "_make_proposer", lambda *a, **k: _DummyProposer())
    result = runner.execute(_options(), env["settings"])
    assert result["status"] == "interrupted"
    record_path = next(
        (env["settings"].runs_root / "dsh" / "adapt" / "morphd-runs").glob("*.json")
    )
    assert json.loads(record_path.read_text(encoding="utf-8"))["status"] == "interrupted"


def test_run_refuses_without_both_budget_gates(env, add_task):
    add_task("demo-1", split="test")
    from morphd.runner import RunError

    try:
        runner.execute(_options(budget_calls={}), env["settings"])
    except RunError as exc:
        assert "budget-calls" in str(exc)
    else:
        raise AssertionError("expected refusal")
