"""Shadow-gated promotion, snapshots, verification and rollback."""

from __future__ import annotations

import difflib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import _upstream, approvals, evaluation, store
from .config import Settings


def _ht():
    return _upstream.core("tree")


PROMOTED_PROFILE_DEFAULT = "md-promoted"


class PromotionError(RuntimeError):
    pass


@dataclass
class SnapshotEntry:
    name: str
    path: Path
    kind: str

    def to_dict(self) -> dict:
        return {"name": self.name, "path": str(self.path), "kind": self.kind}


def _copy_entry(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("node_modules", ".git"))
    elif src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def create_snapshot(
    settings: Settings,
    *,
    runtime: str,
    reason: str,
    entries: dict[str, Path],
    metadata: dict | None = None,
) -> dict:
    entries = {name: Path(path) for name, path in entries.items()}
    base = settings.snapshots_root / runtime / store.utc_stamp()
    ident = base.name
    suffix = 1
    while base.exists():
        suffix += 1
        base = base.with_name(f"{ident}-{suffix}")
    base.mkdir(parents=True)
    manifest_entries = []
    for name, path in sorted(entries.items()):
        exists = path.exists()
        kind = ("dir" if path.is_dir() else "file") if exists else "absent"
        if exists:
            _copy_entry(path, base / "files" / name)
        manifest_entries.append(
            {"name": name, "path": str(path), "kind": kind, "existed": exists}
        )
    manifest = {
        "snapshot": base.name,
        "runtime": runtime,
        "reason": reason,
        "created_at": store.utc_now(),
        "entries": manifest_entries,
        "metadata": metadata or {},
    }
    store.atomic_write_json(base / "manifest.json", manifest)
    return manifest


def update_snapshot_metadata(
    settings: Settings, runtime: str, snapshot: str, extra: dict
) -> dict:
    path = settings.snapshots_root / runtime / snapshot / "manifest.json"
    manifest = store.read_json(path)
    if not isinstance(manifest, dict):
        raise PromotionError(f"snapshot {snapshot!r} is missing")
    manifest.setdefault("metadata", {}).update(extra)
    store.atomic_write_json(path, manifest)
    return manifest


def list_snapshots(settings: Settings, runtime: str) -> list[dict]:
    root = settings.snapshots_root / runtime
    out = []
    if not root.is_dir():
        return out
    for path in sorted(root.iterdir(), reverse=True):
        manifest = store.read_json(path / "manifest.json")
        if isinstance(manifest, dict):
            out.append(manifest)
    return out


def latest_snapshot(settings: Settings, runtime: str) -> dict:
    snaps = list_snapshots(settings, runtime)
    if not snaps:
        raise PromotionError(f"no snapshots exist for {runtime}")
    return snaps[0]


def restore_snapshot(settings: Settings, runtime: str, snapshot: str | None) -> dict:
    manifest = (
        next(
            (s for s in list_snapshots(settings, runtime) if s.get("snapshot") == snapshot),
            None,
        )
        if snapshot
        else None
    )
    if manifest is None:
        manifest = latest_snapshot(settings, runtime)
    root = settings.snapshots_root / runtime / manifest["snapshot"]
    current = {
        f"current-{entry['name']}": Path(entry["path"])
        for entry in manifest.get("entries") or []
        if Path(entry["path"]).exists()
    }
    if current:
        create_snapshot(
            settings,
            runtime=runtime,
            reason="before-rollback",
            entries=current,
            metadata={"rollback_of": manifest["snapshot"]},
        )
    for entry in manifest.get("entries") or []:
        path = Path(entry["path"])
        saved = root / "files" / entry["name"]
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
        if saved.is_dir():
            shutil.copytree(saved, path)
        elif saved.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved, path)
    return manifest


def _neutralize_js_tags(text: str) -> str:
    """Turn `key: !!js <expression>` into a quoted scalar before safe loading."""
    out = []
    pattern = re.compile(r'^(\s*[^#\n][^:\n]*:\s*)!!js\s+(.+?)\s*(?:#.*)?$')
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            out.append(f"{match.group(1)}{json.dumps(match.group(2), ensure_ascii=False)}")
        else:
            out.append(line)
    return "\n".join(out)


def normalize_dump(text: str) -> dict:
    try:
        rows = yaml.safe_load(_neutralize_js_tags(text))
    except yaml.YAMLError as exc:
        raise PromotionError(f"cannot parse dsh --dump-config output: {exc}") from exc
    if not isinstance(rows, list):
        raise PromotionError("dsh --dump-config output is not an entry list")
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = row.get("id")
        if rid is None:
            for child in row.get("insert") or []:
                if isinstance(child, dict) and child.get("id") is not None:
                    normalized.append(
                        {
                            "id": child.get("id"),
                            "name": child.get("name"),
                            "config": child.get("config"),
                            "disabled": child.get("disabled"),
                        }
                    )
            continue
        normalized.append(
            {
                "id": rid,
                "name": row.get("name"),
                "config": row.get("config"),
                "disabled": row.get("disabled"),
            }
        )
    normalized.sort(key=lambda row: str(row.get("id")))
    return {"rows": normalized, "sha256": store.payload_hash(normalized)}


def verify_dsh(adapter, target_home: Path, profile: str) -> dict:
    binary = adapter._binary()
    env = dict(os.environ)
    env["DSH_HOME"] = str(target_home)
    import subprocess

    proc = subprocess.run(
        [binary, "--profile", profile, "--dump-config"],
        cwd=target_home,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if proc.returncode != 0:
        raise PromotionError(
            "promoted dsh profile does not dump: "
            + (proc.stderr or proc.stdout or "")[-1200:].strip()
        )
    norm = normalize_dump(proc.stdout)
    return {
        "dump_ok": True,
        "normalized_sha256": norm["sha256"],
        "rows": len(norm["rows"]),
        "js_rows": sum(
            1
            for row in norm["rows"]
            if isinstance(row.get("config"), dict)
            and any(isinstance(v, str) and ("process." in v or "ctx." in v) for v in row["config"].values())
        ),
    }


def _entry_targets(settings: Settings, runtime: str, target: Path | None, profile: str) -> dict[str, Path]:
    if runtime == "dsh":
        home = target or settings.dsh_target_home()
        if home is None:
            raise PromotionError(
                "dsh promotion needs an explicit target home: pass --target or set "
                "MORPHD_DSH_TARGET_HOME. harness2-morphd never writes the default ~/.dsh."
            )
        if home == Path.home() / ".dsh":
            raise PromotionError("refusing to promote into the default ~/.dsh (third-party collision risk)")
        return {
            "profile": home / "profiles" / profile,
            "home_agents": home / "AGENTS.md",
            "skills": home / "skills",
            "scripts": home / "harness_scripts",
        }
    if target is None:
        raise PromotionError(
            f"{runtime} promotion needs --target (the project directory whose harness files "
            "should be replaced)"
        )
    if target == Path("/"):
        raise PromotionError("refusing to promote into /")
    if runtime == "codex":
        return {
            "agents": target / "AGENTS.md",
            "agents_dir": target / ".agents",
            "scripts": target / "scripts",
            "codex_dir": target / ".codex",
        }
    return {
        "claude_md": target / "CLAUDE.md",
        "claude_dir": target / ".claude" / "skills",
        "claude_agents": target / ".claude" / "agents",
        "claude_settings": target / ".claude" / "settings.json",
        "scripts": target / "scripts",
    }


def _apply_dsh(adapter, tree: dict[str, str], target_home: Path, profile: str) -> None:
    from .runtimes.dsh import _write_profile

    shutil.rmtree(target_home / "skills", ignore_errors=True)
    shutil.rmtree(target_home / "harness_scripts", ignore_errors=True)
    _write_profile(
        target_home,
        profile,
        tree,
        provider=adapter.settings.dsh_provider,
        model=adapter.model,
        include_answerer=False,
    )
    agents = tree.get("AGENTS.md", "")
    if agents:
        store.atomic_write_text(target_home / "AGENTS.md", agents)
    for rel, text in tree.items():
        if rel.startswith("scripts/"):
            store.atomic_write_text(target_home / "harness_scripts" / rel, text)


def _apply_codex(adapter, tree: dict[str, str], target: Path) -> None:
    for rel, text in tree.items():
        if rel == "AGENTS.md" or rel.startswith((".agents/", "scripts/", ".codex/")):
            if rel.endswith("hooks.json"):
                continue
            store.atomic_write_text(target / rel, text)


def _apply_claude(adapter, tree: dict[str, str], target: Path) -> None:
    for rel, text in tree.items():
        if rel == "CLAUDE.md" or rel.startswith(("scripts/", ".claude/skills/")):
            store.atomic_write_text(target / rel, text)
        elif rel.startswith("sub_agents/") and rel.endswith("/AGENT.md"):
            name = rel.split("/")[1]
            store.atomic_write_text(target / ".claude" / "agents" / f"{name}.md", text)
    # Never write .claude/settings.json here: permissions/settings are outside the
    # editable surface and promotion must not change them.


def render_diff(surface, base_tree: dict[str, str], candidate_tree: dict[str, str], *, limit: int = 6000) -> str:
    summary = _ht().diff_summary(surface, base_tree, candidate_tree)
    lines = [
        "# Harness diff",
        "",
        f"- added: {len(summary['added'])}",
        f"- removed: {len(summary['removed'])}",
        f"- modified: {len(summary['modified'])}",
        f"- always-on chars: {summary['always_on_char_delta']:+d}",
        "",
    ]
    for rel in summary["added"]:
        lines.append(f"## + {rel}")
        lines += ["```", candidate_tree.get(rel, "")[:1200], "```", ""]
    for rel in summary["removed"]:
        lines.append(f"## - {rel}")
        lines += ["```", base_tree.get(rel, "")[:1200], "```", ""]
    for rel in summary["modified"]:
        lines.append(f"## ~ {rel}")
        diff = difflib.unified_diff(
            base_tree.get(rel, "").splitlines(),
            candidate_tree.get(rel, "").splitlines(),
            fromfile=f"base/{rel}",
            tofile=f"candidate/{rel}",
            lineterm="",
            n=2,
        )
        block = "\n".join(diff)
        lines += ["```diff", block[:2000], "```", ""]
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit] + "\n…(diff trimmed to one page)\n"
    return text


def promote(
    *,
    adapter,
    settings: Settings,
    candidate: Path,
    eval_report: dict,
    p2: str,
    p3: str,
    phase: str = "m1",
    min_tasks: int | None = None,
    target: Path | None = None,
    profile: str = PROMOTED_PROFILE_DEFAULT,
) -> dict:
    if not p2.strip() or not p3.strip() or p2.strip() == p3.strip():
        raise PromotionError("promotion requires two distinct human approvers (P2 and P3)")
    runtime = adapter.runtime
    if eval_report.get("runtime") != runtime:
        raise PromotionError(
            f"evaluation was produced for {eval_report.get('runtime')!r}, not {runtime!r}"
        )
    candidate = Path(candidate)
    tree = _ht().read_tree(adapter.substrate.surface(), candidate)
    adapter.validate_tree(tree)
    candidate_hash = store.payload_hash(tree)
    if candidate_hash != eval_report.get("candidate_hash"):
        raise PromotionError(
            "candidate hash does not match the evaluated artifact; re-run the evaluation"
        )
    if eval_report.get("model") and eval_report["model"] != adapter.model:
        raise PromotionError(
            "the evaluation used a different model; a model change requires a new evaluation"
        )
    if eval_report.get("permissions_hash") and eval_report["permissions_hash"] != approvals.policy_hash(settings):
        raise PromotionError(
            "approval/security policy changed since the evaluation; re-run the evaluation"
        )
    current_controls = store.payload_hash(adapter.controlled_variables())
    if eval_report.get("controls_hash") and eval_report["controls_hash"] != current_controls:
        raise PromotionError(
            "controlled-variable hash changed since the evaluation (model/runtime/permissions); "
            "re-run the evaluation"
        )
    gate = evaluation.check_gate(eval_report, phase=phase, min_tasks=min_tasks)
    if not gate["passed"]:
        raise PromotionError("promotion gate failed: " + "; ".join(gate["failures"]))
    base_tree = _ht().read_tree(adapter.substrate.surface(), adapter.base_harness())
    diff = render_diff(adapter.substrate.surface(), base_tree, tree)
    entries = _entry_targets(settings, runtime, target, profile)
    base_dir = settings.home / "harnesses" / runtime / "base"
    all_entries = {**entries, "base_harness": base_dir}
    snapshot = create_snapshot(
        settings,
        runtime=runtime,
        reason="before-promote",
        entries=all_entries,
        metadata={"candidate_hash": candidate_hash, "eval": eval_report.get("created_at")},
    )
    try:
        if runtime == "dsh":
            target_home = target or settings.dsh_target_home()
            assert target_home is not None
            _apply_dsh(adapter, tree, target_home, profile)
            verification = verify_dsh(adapter, target_home, profile)
        elif runtime == "codex":
            assert target is not None
            _apply_codex(adapter, tree, target)
            verification = {
                "version": adapter._runtime_version(),
                "tree_sha256": candidate_hash,
            }
        else:
            assert target is not None
            _apply_claude(adapter, tree, target)
            verification = {
                "version": adapter._runtime_version(),
                "tree_sha256": candidate_hash,
            }
        if runtime == "dsh" and "normalized_sha256" in verification:
            update_snapshot_metadata(
                settings,
                runtime,
                snapshot["snapshot"],
                {
                    "normalized_sha256": verification["normalized_sha256"],
                    "profile": profile,
                },
            )
        base_dir.mkdir(parents=True, exist_ok=True)
        _ht().write_tree(adapter.substrate.surface(), tree, base_dir, stubs=True)
    except Exception:
        restore_snapshot(settings, runtime, snapshot["snapshot"])
        raise
    existing = list((settings.promotions_root / runtime).glob("*.json")) if (
        settings.promotions_root / runtime
    ).is_dir() else []
    seq = len(existing) + 1
    record = {
        "promotion_contract": 1,
        "runtime": runtime,
        "seq": seq,
        "created_at": store.utc_now(),
        "candidate": str(candidate),
        "candidate_hash": candidate_hash,
        "eval_created_at": eval_report.get("created_at"),
        "eval_n": eval_report.get("n_tasks"),
        "gate": gate,
        "approvals": {
            "p2": p2.strip(),
            "p3": p3.strip(),
            "approved_at": store.utc_now(),
        },
        "target": str(target) if target else str(settings.dsh_target_home()),
        "profile": profile if runtime == "dsh" else "",
        "snapshot": snapshot["snapshot"],
        "verification": verification,
        "diff_summary": _ht().diff_summary(adapter.substrate.surface(), base_tree, tree),
        "diff": diff,
        "model": adapter.model,
        "controls_hash": store.payload_hash(adapter.controlled_variables()),
    }
    path = settings.promotions_root / runtime / f"p{seq:03d}-{store.utc_stamp()}.json"
    store.atomic_write_json(path, record)
    store.atomic_write_json(settings.promotions_root / runtime / "HEAD.json", record)
    record["record_path"] = str(path)
    record["snapshot"] = snapshot["snapshot"]
    return record


def rollback(adapter, settings: Settings, snapshot: str | None = None) -> dict:
    manifest = restore_snapshot(settings, adapter.runtime, snapshot)
    verification: dict = {}
    if adapter.runtime == "dsh":
        target_home = settings.dsh_target_home()
        if target_home is None:
            raise PromotionError("cannot verify dsh rollback without MORPHD_DSH_TARGET_HOME")
        head = store.read_json(settings.promotions_root / "dsh" / "HEAD.json") or {}
        profile = str(head.get("profile") or PROMOTED_PROFILE_DEFAULT)
        profile_dir = target_home / "profiles" / profile
        if profile_dir.is_dir():
            verification = verify_dsh(adapter, target_home, profile)
            expected = (manifest.get("metadata") or {}).get("normalized_sha256")
            if expected and expected != verification["normalized_sha256"]:
                raise PromotionError(
                    "rollback verification failed: normalized dsh config does not match the "
                    f"snapshot ({expected} != {verification['normalized_sha256']})"
                )
        else:
            verification = {"dump_ok": False, "skipped": "profile absent after rollback"}
    return {"restored": manifest["snapshot"], "verification": verification}
