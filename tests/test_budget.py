from __future__ import annotations

import pytest

from morphd import budget, pricing


def test_usage_normalisation_and_cost(env):
    price = env["price"]
    dsh = pricing.normalize_usage(
        "dsh", {"inputTokens": 1000, "outputTokens": 200, "cacheReadTokens": 500}
    )
    assert dsh["input_tokens"] == 1000
    assert dsh["cached_input_tokens"] == 500
    assert price.usage_cost("dsh", dsh) > 0
    codex = pricing.normalize_usage(
        "codex",
        {
            "input_tokens": 1000,
            "cached_input_tokens": 400,
            "output_tokens": 200,
            "reasoning_output_tokens": 50,
        },
    )
    assert codex["cached_input_tokens"] == 400
    assert price.usage_cost("codex", codex) > 0
    claude = pricing.normalize_usage(
        "claude-code", {"input_tokens": 10, "total_cost_usd": 0.42}
    )
    assert price.usage_cost("claude-code", claude) == 0.42
    assert price.usage_cost("dsh", None) is None


def test_budget_guard_journal_and_stop(tmp_path):
    journal = tmp_path / "budget.jsonl"
    guard = budget.BudgetGuard(limits=budget.Limits(usd=0.0001, calls=2), journal=journal)
    guard.reserve(kind="rollout", label="one")
    guard.charge(0.00005, kind="rollout", label="one")
    guard.reserve(kind="rollout", label="two")
    guard.charge(0.00006, kind="rollout", label="two")
    assert guard.exceeded
    with pytest.raises(budget.BudgetExceeded):
        guard.reserve(kind="rollout", label="three")
    reloaded = budget.BudgetGuard(limits=budget.Limits(usd=1, calls=10), journal=journal)
    assert reloaded.calls == 2
    assert reloaded.usd == pytest.approx(0.00011)


def test_budget_call_limit_and_release(tmp_path):
    guard = budget.BudgetGuard(limits=budget.Limits(calls=1))
    guard.reserve(kind="session", label="x")
    guard.release(kind="session", label="x")
    guard.reserve(kind="session", label="y")
    with pytest.raises(budget.BudgetExceeded):
        guard.reserve(kind="session", label="z")


def test_plan_refuses_before_start(env):
    price = env["price"]
    row = budget.estimate_run(
        runtime="dsh",
        turn_counts=[1, 2],
        k=1,
        price=price,
        limits=budget.Limits(usd=0.000001, calls=1),
        proposer_channel="opencode",
    )
    assert row.total_calls == 4 * 3 + 3 * 2
    with pytest.raises(budget.BudgetError):
        budget.check_plan([row])


def test_shared_budget_uses_one_guard(env):
    rb = budget.RunBudget(
        settings=env["settings"],
        limits_by_runtime={
            "dsh": budget.Limits(usd=1, calls=5, label="dsh"),
            "codex": budget.Limits(usd=2, calls=6, label="codex"),
        },
        shared=True,
        namespace="test-shared",
        pricing_snapshot={"v": 1},
    )
    a = rb.guard_for("dsh")
    b = rb.guard_for("codex")
    assert a is b
    assert a.limits.usd == 3
    assert a.limits.calls == 11
    assert a.journal and "_budget" in str(a.journal)


def test_run_budget_per_runtime_journals(env):
    rb = budget.RunBudget(
        settings=env["settings"],
        limits_by_runtime={
            "dsh": budget.Limits(usd=1, calls=5),
            "codex": budget.Limits(usd=1, calls=5),
        },
        shared=False,
        namespace="ns",
    )
    a = rb.guard_for("dsh")
    b = rb.guard_for("codex")
    assert a is not b
    assert "dsh" in str(a.journal)
    assert "codex" in str(b.journal)
