from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from morphd import contract, evaluation, metrics, runtimes
from morphd import promote as promotion
from morphd.budget import BudgetGuard, Limits


def _adapter(env, guard=None):
    return runtimes.build_adapter(
        "dsh",
        tasks=contract.TaskStore(env["settings"].tasks_root),
        settings=env["settings"],
        price=env["price"],
        guard=guard or BudgetGuard(limits=Limits(usd=10, calls=100)),
        model="deepseek-flash",
    )


def _candidate(adapter, tmp_path) -> Path:
    base = adapter.base_harness()
    candidate = tmp_path / "candidate-harness"
    shutil.copytree(base, candidate)
    agents = (candidate / "AGENTS.md").read_text(encoding="utf-8")
    (candidate / "AGENTS.md").write_text(agents + "\nMORPHD_IMPROVED\n", encoding="utf-8")
    return candidate


def _tasks(add_task, count: int = 2):
    for i in range(count):
        add_task(f"demo-{i}", instruction=f"write hello {i}", split="test")


def test_shadow_evaluation_detects_improvement(env, add_task, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DSH_REQUIRE_MARKER", "1")
    _tasks(add_task)
    adapter = _adapter(env)
    candidate = _candidate(adapter, tmp_path)
    guard = BudgetGuard(
        limits=Limits(usd=10, calls=100),
        journal=tmp_path / "eval-budget.jsonl",
    )
    adapter.guard = guard
    report = evaluation.run_evaluation(
        adapter=adapter,
        tasks_store=contract.TaskStore(env["settings"].tasks_root),
        options=evaluation.EvaluationOptions(splits=("test",)),
        candidate=candidate,
        guard=guard,
        price=env["price"],
        settings=env["settings"],
        stamp="eval1",
    )
    assert report["status"] == "done"
    assert report["n_tasks"] == 2
    achieved = report["deltas"]["achieved"]
    assert achieved["base"] == 0.0
    assert achieved["candidate"] == 1.0
    assert achieved["delta"] == 1.0
    assert report["deltas"]["delivered"]["delta"] == 1.0
    gate = evaluation.check_gate(report, phase="m1", min_tasks=2)
    assert gate["passed"], gate
    strict_gate = evaluation.check_gate(report, phase="m1")
    assert not strict_gate["passed"]  # fewer than the PRD's 20 tasks
    md = evaluation.render_markdown(report, gate)
    assert "dsh" in md and "candidate" in md
    assert "Do not compare absolute values" in md


def test_gate_rejects_small_n_and_regression_reports():
    base = [{"achieved": False, "delivered": False, "domain": "d"} for _ in range(20)]
    candidate = [{"achieved": True, "delivered": True, "domain": "d"} for _ in range(20)]
    report = {
        "status": "done",
        "n_tasks": 20,
        "permissions_hash": "fixed",
        "deltas": {
            "achieved": metrics.paired_delta(base, candidate, "achieved"),
            "delivered": metrics.paired_delta(base, candidate, "delivered"),
        },
        "domains": {
            "d": {
                "achieved_delta": metrics.paired_delta(base, candidate, "achieved"),
                "delivered_delta": metrics.paired_delta(base, candidate, "delivered"),
            }
        },
    }
    assert evaluation.check_gate(report, phase="m1")["passed"] is True
    base_r = base[:10] + [{"achieved": True, "delivered": True, "domain": "e"} for _ in range(10)]
    cand_r = candidate[:10] + [{"achieved": False, "delivered": False, "domain": "e"} for _ in range(10)]
    regressed = {
        "status": "done",
        "n_tasks": 20,
        "permissions_hash": "fixed",
        "deltas": {
            "achieved": metrics.paired_delta(base_r, cand_r, "achieved"),
            "delivered": metrics.paired_delta(base_r, cand_r, "delivered"),
        },
        "domains": metrics.domain_breakdown(base_r, cand_r, ["d", "e"]),
    }
    assert evaluation.check_gate(regressed, phase="m1")["passed"] is False
    small = dict(report)
    small["n_tasks"] = 5
    assert evaluation.check_gate(small, phase="m1")["passed"] is False
    delivered_regression = {
        **report,
        "deltas": {
            **report["deltas"],
            "delivered": {"n": 20, "base": 1.0, "candidate": 0.9, "delta": -0.1},
        },
    }
    assert evaluation.check_gate(delivered_regression, phase="m1")["passed"] is False
    approval_drift = {
        **report,
        "approvals": {
            "base": {"n": 10, "allowed": 5, "rejected": 5, "allow_rate": 0.5},
            "candidate": {"n": 10, "allowed": 8, "rejected": 2, "allow_rate": 0.8},
        },
    }
    result = evaluation.check_gate(approval_drift, phase="m1")
    assert result["passed"] is False
    assert any("allow-rate" in failure for failure in result["failures"])


def test_m2_gate_requires_50_and_twenty_percent():
    base = [
        {"achieved": i < 25, "delivered": True, "domain": "d"} for i in range(50)
    ]
    candidate = [
        {"achieved": i < 40, "delivered": True, "domain": "d"} for i in range(50)
    ]
    report = {
        "status": "done",
        "n_tasks": 50,
        "permissions_hash": "fixed",
        "deltas": {
            "achieved": metrics.paired_delta(base, candidate, "achieved"),
            "delivered": metrics.paired_delta(base, candidate, "delivered"),
        },
        "domains": {
            "d": {
                "achieved_delta": metrics.paired_delta(base, candidate, "achieved"),
                "delivered_delta": metrics.paired_delta(base, candidate, "delivered"),
            }
        },
    }
    gate = evaluation.check_gate(report, phase="m2")
    assert gate["passed"], gate
    report["n_tasks"] = 49
    assert evaluation.check_gate(report, phase="m2")["passed"] is False


def test_promotion_and_rollback(env, add_task, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DSH_REQUIRE_MARKER", "1")
    target = tmp_path / "dsh-target"
    monkeypatch.setenv("MORPHD_DSH_TARGET_HOME", str(target))
    _tasks(add_task)
    adapter = _adapter(env)
    candidate = _candidate(adapter, tmp_path)
    guard = BudgetGuard(limits=Limits(usd=10, calls=100))
    adapter.guard = guard
    report = evaluation.run_evaluation(
        adapter=adapter,
        tasks_store=contract.TaskStore(env["settings"].tasks_root),
        options=evaluation.EvaluationOptions(splits=("test",)),
        candidate=candidate,
        guard=guard,
        price=env["price"],
        settings=env["settings"],
        stamp="promo",
    )
    with pytest.raises(promotion.PromotionError, match="distinct"):
        promotion.promote(
            adapter=adapter,
            settings=env["settings"],
            candidate=candidate,
            eval_report=report,
            p2="same",
            p3="same",
            phase="m1",
            min_tasks=2,
            target=target,
        )
    record = promotion.promote(
        adapter=adapter,
        settings=env["settings"],
        candidate=candidate,
        eval_report=report,
        p2="alice",
        p3="bob",
        phase="m1",
        min_tasks=2,
        target=target,
    )
    assert record["verification"]["dump_ok"] is True
    profile = target / "profiles" / "md-promoted"
    assert (profile / "cordis.patch.yml").is_file()
    assert "MORPHD_IMPROVED" in (target / "AGENTS.md").read_text(encoding="utf-8")
    base_dir = env["settings"].home / "harnesses" / "dsh" / "base"
    assert (base_dir / "AGENTS.md").is_file()
    assert (env["settings"].promotions_root / "dsh" / "HEAD.json").is_file()
    result = promotion.rollback(adapter, env["settings"])
    assert result["restored"]
    assert not profile.exists()
    assert not base_dir.exists()


def test_control_drift_is_rejected(env, add_task, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DSH_REQUIRE_MARKER", "1")
    target = tmp_path / "dsh-target"
    monkeypatch.setenv("MORPHD_DSH_TARGET_HOME", str(target))
    _tasks(add_task)
    adapter = _adapter(env)
    candidate = _candidate(adapter, tmp_path)
    guard = BudgetGuard(limits=Limits(usd=10, calls=100))
    adapter.guard = guard
    report = evaluation.run_evaluation(
        adapter=adapter,
        tasks_store=contract.TaskStore(env["settings"].tasks_root),
        options=evaluation.EvaluationOptions(splits=("test",)),
        candidate=candidate,
        guard=guard,
        price=env["price"],
        settings=env["settings"],
        stamp="promo3",
    )
    report["controls_hash"] = "0" * 64
    with pytest.raises(promotion.PromotionError, match="controlled-variable"):
        promotion.promote(
            adapter=adapter,
            settings=env["settings"],
            candidate=candidate,
            eval_report=report,
            p2="alice",
            p3="bob",
            phase="m1",
            min_tasks=2,
            target=target,
        )


def test_mismatched_candidate_hash_is_rejected(env, add_task, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_DSH_REQUIRE_MARKER", "1")
    target = tmp_path / "dsh-target"
    monkeypatch.setenv("MORPHD_DSH_TARGET_HOME", str(target))
    _tasks(add_task)
    adapter = _adapter(env)
    candidate = _candidate(adapter, tmp_path)
    guard = BudgetGuard(limits=Limits(usd=10, calls=100))
    adapter.guard = guard
    report = evaluation.run_evaluation(
        adapter=adapter,
        tasks_store=contract.TaskStore(env["settings"].tasks_root),
        options=evaluation.EvaluationOptions(splits=("test",)),
        candidate=candidate,
        guard=guard,
        price=env["price"],
        settings=env["settings"],
        stamp="promo2",
    )
    report["candidate_hash"] = "0" * 64
    with pytest.raises(promotion.PromotionError, match="hash"):
        promotion.promote(
            adapter=adapter,
            settings=env["settings"],
            candidate=candidate,
            eval_report=report,
            p2="alice",
            p3="bob",
            phase="m1",
            min_tasks=2,
            target=target,
        )
