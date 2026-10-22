"""Runtime-scoped experience-library metadata, expiry and index repair."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import _upstream, config, store
from .config import Settings


def _lib():
    return _upstream.core("library")


def surface_for(runtime: str):
    if runtime == "dsh":
        from .runtimes.dsh import DSH_SURFACE

        return DSH_SURFACE
    if runtime == "claude-code":
        from .runtimes.claudecode import CLAUDE_SURFACE

        return CLAUDE_SURFACE
    if runtime == "codex":
        from .runtimes.codex import CX

        return CX.CODEX_SURFACE
    raise ValueError(f"unknown runtime {runtime!r}")


def runtime_fingerprint(settings: Settings, runtime: str) -> str:
    payload = {
        "runtime": runtime,
        "pin": settings.pins.get(runtime),
        "model": settings.model_for(runtime),
        "provider": settings.dsh_provider if runtime == "dsh" else "",
        "effort": (
            settings.codex_reasoning_effort
            if runtime == "codex"
            else settings.claude_effort
            if runtime == "claude-code"
            else ""
        ),
        "permission": (
            settings.claude_permission_mode
            if runtime == "claude-code"
            else config.DSH_SANDBOX_MODE
            if runtime == "dsh"
            else "bypass"
        ),
        "approver_tools": settings.approver_tools,
        "approver_deny": settings.approver_deny,
    }
    return store.payload_hash(payload)


def fingerprint_from_runs(settings: Settings, runtime: str) -> str:
    """The control hash actually stamped by the latest run, else a settings-only fallback."""
    candidates: list[tuple[str, dict]] = []
    identity_root = settings.runs_root / runtime / "_identity"
    if identity_root.is_dir():
        for path in identity_root.glob("*.json"):
            payload = store.read_json(path)
            if isinstance(payload, dict) and payload.get("controls_hash"):
                candidates.append((str(payload.get("written_at") or path.name), payload))
    run_root = settings.runs_root / runtime / "adapt" / "morphd-runs"
    if run_root.is_dir():
        for path in run_root.glob("*.json"):
            payload = store.read_json(path)
            if isinstance(payload, dict) and payload.get("controls_hash"):
                candidates.append(
                    (str(payload.get("finished_at") or payload.get("started_at") or path.name), payload)
                )
    if candidates:
        candidates.sort(key=lambda item: item[0])
        return str(candidates[-1][1]["controls_hash"])
    return runtime_fingerprint(settings, runtime)


def entry_dirs(root: Path) -> list[Path]:
    root = Path(root)
    out: list[Path] = []
    if not root.is_dir():
        return out
    for mode_dir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        for domain_dir in sorted(p for p in mode_dir.iterdir() if p.is_dir()):
            harnesses = domain_dir / "harnesses"
            if not harnesses.is_dir():
                continue
            for task_dir in sorted(p for p in harnesses.iterdir() if p.is_dir()):
                out.append(task_dir)
    return out


def stamp_entries(root: Path, *, runtime: str, fingerprint: str, run_id: str) -> int:
    stamped = 0
    for entry in entry_dirs(root):
        store.atomic_write_json(
            entry / ".morphd-entry.json",
            {
                "runtime": runtime,
                "fingerprint": fingerprint,
                "run_id": run_id,
                "stamped_at": store.utc_now(),
            },
        )
        stamped += 1
    return stamped


def _rebuild_index(entry: Path, surface) -> None:
    domain_root = entry.parents[1]
    mode_root = domain_root.parent
    try:
        _lib().Library(root=mode_root, domain=domain_root.name, surface=surface).rebuild_index()
    except Exception:
        index = domain_root / "INDEX.md"
        if index.exists():
            index.unlink()


def expire(
    root: Path,
    *,
    runtime: str,
    current_fingerprint: str,
    dry_run: bool = False,
) -> dict:
    surface = surface_for(runtime)
    keep: list[str] = []
    stale: list[dict] = []
    for entry in entry_dirs(root):
        stamp = store.read_json(entry / ".morphd-entry.json") or {}
        reason = ""
        if stamp.get("runtime") != runtime:
            reason = "runtime-mismatch"
        elif stamp.get("fingerprint") != current_fingerprint:
            reason = "fingerprint-changed" if stamp else "unstamped"
        if reason:
            stale.append({"entry": str(entry), "reason": reason})
            if not dry_run:
                relative = entry.relative_to(root)
                dest = root / "_stale" / store.utc_stamp() / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(entry), str(dest))
                _rebuild_index(entry, surface)
        else:
            keep.append(str(entry))
    return {
        "runtime": runtime,
        "fingerprint": current_fingerprint,
        "kept": keep,
        "stale": stale,
        "dry_run": dry_run,
    }


def stats(root: Path) -> dict:
    cutoffs: dict[str, int] = {"current": 0, "unstamped": 0, "quarantined": 0}
    for entry in entry_dirs(root):
        stamp = store.read_json(entry / ".morphd-entry.json") or {}
        if stamp:
            cutoffs["current"] += 1
        else:
            cutoffs["unstamped"] += 1
    cutoffs["quarantined"] = len(list(Path(root).glob("_stale/*/*/*/harnesses/*")))
    return {"root": str(root), **cutoffs}
