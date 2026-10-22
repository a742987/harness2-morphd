"""Runtime parity diagnostics for the task set. Never a cross-runtime score ranking."""

from __future__ import annotations

from itertools import combinations
from pathlib import Path

from . import contract, store
from .config import Settings


def _latest_runs(settings: Settings, runtime: str) -> dict[str, dict]:
    root = settings.runs_root / runtime / "rollouts"
    chosen: dict[str, tuple[str, dict]] = {}
    if not root.is_dir():
        return {}
    for meta_path in sorted(root.rglob("meta.json")):
        meta = store.read_json(meta_path)
        if not isinstance(meta, dict):
            continue
        task = str(meta.get("task_ref") or "")
        if not task:
            continue
        finished = str(meta.get("finished_at") or meta.get("started_at") or "")
        if task not in chosen or finished >= chosen[task][0]:
            chosen[task] = (finished, {**meta, "_run_dir": str(meta_path.parent)})
    return {task: meta for task, (_, meta) in chosen.items()}


def collect(settings: Settings, runtimes: list[str]) -> dict:
    try:
        total_tasks = len(contract.TaskStore(settings.tasks_root, settings.split_file).all())
    except contract.ContractError:
        total_tasks = 0
    latest = {runtime: _latest_runs(settings, runtime) for runtime in runtimes}
    per_runtime: dict[str, dict] = {}
    verdicts_by_runtime: dict[str, dict[str, bool]] = {}
    for runtime, runs in latest.items():
        done = {task: meta for task, meta in runs.items() if meta.get("status") == "done"}
        converged = 0
        verdicts: dict[str, bool] = {}
        for task, meta in done.items():
            replay = store.read_json(Path(meta["_run_dir"]) / "replay.json") or {}
            if replay.get("convergence_turns") is not None:
                converged += 1
            if isinstance(meta.get("achieved"), bool):
                verdicts[task] = meta["achieved"]
        per_runtime[runtime] = {
            "n_tasks_seen": len(runs),
            "n_done": len(done),
            "task_available": round(len(done) / total_tasks, 4) if total_tasks else None,
            "replay_converged": round(converged / len(done), 4) if done else None,
        }
        verdicts_by_runtime[runtime] = verdicts
    pairs: dict[str, dict] = {}
    for left, right in combinations(runtimes, 2):
        lset = set(verdicts_by_runtime[left])
        rset = set(verdicts_by_runtime[right])
        common = lset & rset
        agree = sum(
            1
            for task in common
            if verdicts_by_runtime[left][task] == verdicts_by_runtime[right][task]
        )
        skew = set(latest[left]) ^ set(latest[right])
        pairs[f"{left}|{right}"] = {
            "verdict_agree": round(agree / len(common), 4) if common else None,
            "n_common_verdicts": len(common),
            "adapter_skew": len(skew),
            "adapter_skew_examples": sorted(skew)[:10],
        }
    return {
        "parity_contract": 1,
        "runtimes": per_runtime,
        "pairs": pairs,
        "note": (
            "Parity diagnoses the task set. verdict_agree is not a gate and must not be used "
            "to rank runtimes; semantic differences are expected."
        ),
    }


__all__ = ["collect"]
