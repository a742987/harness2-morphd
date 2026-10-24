from __future__ import annotations

from pathlib import Path

from morphd import budget
from morphd.byok_proposer import BYOKUpstreamProposer
from morphd.consumer.providers import Completion
from morphd.consumer.store import ConsumerStore


def test_byok_bridge_returns_upstream_reply_and_charges_guard(env, tmp_path, monkeypatch):
    import morphd.byok_proposer as bridge

    home = tmp_path / "consumer-home"
    store = ConsumerStore(home)

    class FakeProviders:
        def clients_for_role(self, settings, role):
            return [object()]

    class FakeConsumer:
        def __init__(self):
            self.store = store
            self.providers = FakeProviders()

    monkeypatch.setattr(bridge, "ConsumerService", lambda _home: FakeConsumer())

    class FakeInner:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def call(self, mission, seed, label, **kwargs):
            self.kwargs["precall"]("propose-tool-0")
            charge = self.kwargs["charge"]
            charge(
                Completion(
                    text="done",
                    usage={"present": True, "input_tokens": 10, "output_tokens": 2},
                    cost_usd=0.01,
                    cost_estimated=False,
                ),
                "propose-tool-0",
            )
            return {
                "ok": True,
                "summary": "done",
                "workspace": str(seed),
                "cost_usd": 0.01,
                "attempts": 1,
                "cached": False,
                "text": "done",
            }

    monkeypatch.setattr(bridge, "BYOKProposer", FakeInner)
    guard = budget.BudgetGuard(limits=budget.Limits(usd=1.0, calls=10))
    prop = BYOKUpstreamProposer(
        spec=object(),
        adapter=None,
        guard=guard,
        price=env["price"],
        audit_dir=tmp_path / "audit",
    )
    context = tmp_path / "context"
    harness = context / "context" / "harness"
    harness.mkdir(parents=True)
    (harness / "AGENTS.md").write_text("# Guidance\n", encoding="utf-8")
    reply = prop.call("mission", context, "label.a1", harvest="context/harness")
    assert reply.ok is True
    assert reply.text == "done"
    assert Path(reply.workspace) == harness
    assert guard.usd == 0.01
    assert guard.snapshot()["calls"] == 1
