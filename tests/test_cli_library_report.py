from __future__ import annotations

import json
import shutil

from morphd import approvals, contract, library, report, runtimes, store
from morphd.budget import BudgetGuard, Limits
from morphd.cli.main import main


def _adapter(env):
    return runtimes.build_adapter(
        "dsh",
        tasks=contract.TaskStore(env["settings"].tasks_root),
        settings=env["settings"],
        price=env["price"],
        guard=BudgetGuard(limits=Limits(usd=10, calls=100)),
        model="deepseek-flash",
    )


def test_dry_run_is_zero_write(env, add_task, tmp_path, capsys, monkeypatch):
    add_task("demo-1", split="test")
    new_home = tmp_path / "untouched-home"
    code = main(
        [
            "--runtime",
            "dsh",
            "--tasks-root",
            str(env["settings"].tasks_root),
            "--home",
            str(new_home),
            "--split",
            "test",
            "--dry-run",
            "--model",
            "dsh=deepseek-flash",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "dry-run" in out
    assert "task stream" in out
    assert not new_home.exists()


def test_doctor_no_probe_and_tasks_cli(env, add_task, capsys, monkeypatch):
    add_task("demo-1", split="test")
    monkeypatch.setenv("MORPHD_CONTAINER_ISOLATED", "1")
    monkeypatch.setenv("MORPHD_EGRESS_ENFORCED", "1")
    monkeypatch.setenv("MORPHD_EGRESS_ALLOWLIST", "api.example.com")
    code = main(["doctor", "--no-probe", "--home", str(env["settings"].home)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "python" in out
    code = main(
        ["tasks", "list", "--tasks-root", str(env["settings"].tasks_root), "--json"]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "demo-1" in out
    code = main(["tasks", "validate", "--tasks-root", str(env["settings"].tasks_root)])
    assert code == 0


def test_evaluate_cli(env, add_task, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FAKE_DSH_REQUIRE_MARKER", "1")
    add_task("demo-1", split="test")
    adapter = _adapter(env)
    candidate = tmp_path / "candidate"
    shutil.copytree(adapter.base_harness(), candidate)
    agents = (candidate / "AGENTS.md").read_text(encoding="utf-8")
    (candidate / "AGENTS.md").write_text(agents + "\nMORPHD_IMPROVED\n", encoding="utf-8")
    out = tmp_path / "eval.json"
    code = main(
        [
            "evaluate",
            "--runtime",
            "dsh",
            "--candidate",
            str(candidate),
            "--split",
            "test",
            "--budget-usd",
            "10",
            "--budget-calls",
            "100",
            "--model",
            "deepseek-flash",
            "--out",
            str(out),
            "--home",
            str(env["settings"].home),
            "--tasks-root",
            str(env["settings"].tasks_root),
        ]
    )
    assert code == 0, capsys.readouterr().err
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["runtime"] == "dsh"
    assert payload["deltas"]["achieved"]["delta"] == 1.0


def test_report_and_judge(env, add_task):
    add_task("demo-1")
    adapter = _adapter(env)
    adapter.preflight()
    adapter.execute("demo-1", None, "report-run")
    data = report.collect(env["settings"], ["dsh"])
    assert data["runtimes"]["dsh"]["metrics"]["achievement_rate"] == 1.0
    md = report.render_markdown(data)
    assert "## dsh" in md
    assert "Cost by day" in md
    judged = report.judge_runs(adapter=adapter, settings=env["settings"])
    assert judged["scored"] == 1
    second = report.judge_runs(adapter=adapter, settings=env["settings"])
    assert second["skipped"] == 1
    path = env["settings"].runs_root / "dsh" / "rollouts" / "demo-1" / "report-run" / "judge.json"
    assert json.loads(path.read_text(encoding="utf-8"))["all_pass"] is True


def test_library_stamp_and_expiry(env):
    root = env["settings"].runtime_library_root("dsh")
    entry = root / "agg" / "demo" / "harnesses" / "task-1"
    (entry / "harness").mkdir(parents=True)
    (entry / "harness" / "AGENTS.md").write_text("# guidance\n", encoding="utf-8")
    (entry / "EXPERIENCE.md").write_text("# task-1\n\n- **cue**: demo cue\n", encoding="utf-8")
    stamped = library.stamp_entries(root, runtime="dsh", fingerprint="fp1", run_id="run1")
    assert stamped == 1
    result = library.expire(root, runtime="dsh", current_fingerprint="fp1")
    assert result["stale"] == []
    assert entry.is_dir()
    result = library.expire(root, runtime="dsh", current_fingerprint="fp2")
    assert result["stale"]
    assert not entry.exists()
    assert (root / "_stale").is_dir()
    assert library.stats(root)["current"] == 0


def test_approver_plugin_and_distribution(tmp_path, env):
    plugin = approvals.install_plugin(tmp_path / "morphd-approver.mjs")
    text = plugin.read_text(encoding="utf-8")
    assert "approval/request" in text
    policy = approvals.write_policy(tmp_path / "policy.json", env["settings"])
    assert policy.is_file()
    log = tmp_path / "approvals.jsonl"
    store.append_jsonl(log, {"decision": "allowed-once", "rule": "allowlisted"})
    store.append_jsonl(log, {"decision": "rejected", "rule": "deny-pattern"})
    dist = approvals.distribution(log)
    assert dist["n"] == 2 and dist["allow_rate"] == 0.5
    drift = approvals.drift({"allow_rate": 0.8}, dist)
    assert drift["drift"] is True
