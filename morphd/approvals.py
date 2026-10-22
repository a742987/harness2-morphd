"""The controlled dsh approval answerer and its audit trail."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from . import config, store
from .config import Settings

PLUGIN_FILENAME = "morphd-approver.mjs"


def write_policy(path: Path, settings: Settings) -> Path:
    policy = {
        "mode": "allowlist",
        "allow_tools": [s.strip() for s in settings.approver_tools.split(",") if s.strip()],
        "deny_command_patterns": [
            s.strip() for s in settings.approver_deny.split(",") if s.strip()
        ],
        "allow_command_prefixes": [],
        "deny_network": True,
    }
    store.atomic_write_json(path, policy)
    return path


def install_plugin(destination: Path) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config.default_approver_plugin(), destination)
    return destination


def read_decisions(path: Path) -> list[dict]:
    return store.read_jsonl(Path(path))


def distribution(path: Path) -> dict:
    rows = read_decisions(path)
    allowed = sum(1 for row in rows if row.get("decision") == "allowed-once")
    rejected = sum(1 for row in rows if row.get("decision") == "rejected")
    total = allowed + rejected
    return {
        "n": total,
        "allowed": allowed,
        "rejected": rejected,
        "allow_rate": round(allowed / total, 4) if total else None,
        "rules": _rule_counts(rows),
    }


def _rule_counts(rows: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        rule = str(row.get("rule") or "unknown")
        counts[rule] = counts.get(rule, 0) + 1
    return dict(sorted(counts.items()))


def drift(baseline: dict, candidate: dict, *, threshold: float = 0.10) -> dict:
    b = baseline.get("allow_rate")
    c = candidate.get("allow_rate")
    if b is None or c is None:
        return {"comparable": False, "delta": None, "drift": False}
    delta = round(c - b, 4)
    return {
        "comparable": True,
        "baseline_allow_rate": b,
        "candidate_allow_rate": c,
        "delta": delta,
        "drift": abs(delta) > threshold,
        "threshold": threshold,
    }


def policy_hash(settings: Settings) -> str:
    payload = {
        "allow_tools": sorted(s.strip() for s in settings.approver_tools.split(",") if s.strip()),
        "deny": sorted(s.strip() for s in settings.approver_deny.split(",") if s.strip()),
        "deny_network": True,
    }
    return store.payload_hash(json.loads(json.dumps(payload)))
