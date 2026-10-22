from __future__ import annotations

import json

from morphd import contract, parity, report, runtimes
from morphd.budget import BudgetGuard, Limits


def _adapter(env, runtime):
    return runtimes.build_adapter(
        runtime,
        tasks=contract.TaskStore(env["settings"].tasks_root),
        settings=env["settings"],
        price=env["price"],
        guard=BudgetGuard(limits=Limits(usd=10, calls=100)),
        model="deepseek-flash" if runtime == "dsh" else "fake-model",
    )


def test_parity_and_report_labels(env, add_task):
    add_task("demo-1")
    for runtime in ("dsh", "codex"):
        adapter = _adapter(env, runtime)
        adapter.preflight()
        run_dir = adapter.execute("demo-1", None, f"{runtime}-parity-run")
        assert json.loads((run_dir / "metrics.json").read_text())["achieved"] is True
    data = parity.collect(env["settings"], ["dsh", "codex"])
    assert data["runtimes"]["dsh"]["task_available"] == 1.0
    assert data["runtimes"]["dsh"]["replay_converged"] == 1.0
    pair = data["pairs"]["dsh|codex"]
    assert pair["verdict_agree"] == 1.0
    assert pair["adapter_skew"] == 0
    report_data = report.collect(env["settings"], ["dsh", "codex"])
    assert "parity" in report_data
    assert report_data["runtimes"]["dsh"]["by_label"]
    md = report.render_markdown(report_data)
    assert "Runtime parity" in md
    assert "By arm" in md
    assert "Do not compare" in md or "Runtime-internal" in md


def test_online_report(tmp_path):
    from conftest import write_session

    from morphd import online

    cwd = tmp_path / "project"
    cwd.mkdir()
    events = [
        {"type": "turn/start", "seq": 0, "time": 1000, "data": {"turn": 1}},
        {
            "type": "user/message",
            "seq": 1,
            "time": 1000,
            "data": {
                "role": "user",
                "id": "u1",
                "content": [{"type": "text", "text": "do it"}],
                "source": {"kind": "user"},
            },
        },
        {"type": "turn/end", "seq": 2, "time": 2000, "data": {"turn": 1, "reason": {"kind": "completed"}}},
    ]
    path = tmp_path / "sessions" / "--project--" / "s1" / "session.v3.jsonl"
    write_session(path, sid="s1", cwd=cwd, events=events, compressed=False)
    data = online.collect(tmp_path / "sessions")
    assert data["n_sessions"] == 1
    assert data["correction_session_rate"] == 0.0
    assert data["intervention_rate"] == 0.0
    assert data["duration_p95_ms"] == 1000.0
    assert "Online arm" in online.render_markdown(data)
