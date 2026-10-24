"""Session analysis: triage, task construction, BYOK proposal and candidate generation."""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from .. import contract, sanitize
from .. import store as engine_store
from . import APP_VERSION
from .attention import AttentionManager
from .budget import ConsumerBudget
from .candidates import CandidateManager
from .errors import ConsumerError
from .hosts import HostManager
from .policy import APPETITES, candidate_confidence, check_candidate, risk_statement
from .privacy import preview_payload
from .proposer import BYOKProposer
from .providers import ProvidersManager, estimate_analysis_usd
from .settings import (
    ProjectManager,
    SettingsManager,
    effective_settings,
)
from .store import ConsumerStore
from .util import copy_context, excluded, new_id, read_tree


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _clips(text: str, limit: int = 1200) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _present_after(session: dict, settings: dict) -> str:
    """Delay active presentation after session end without delaying analysis."""
    try:
        delay = int(settings.get("cadence", {}).get("idle_delay_minutes", 0) or 0)
    except (TypeError, ValueError):
        delay = 0
    delay = max(0, min(30, delay))
    try:
        ended = datetime.fromisoformat(str(session.get("ended_at") or "").replace("Z", "+00:00"))
    except ValueError:
        ended = datetime.now().astimezone()
    if ended.tzinfo is None:
        ended = ended.astimezone()
    return (ended + timedelta(minutes=delay)).isoformat(timespec="seconds")


def _expected_effect(kind: str, signals: dict) -> str:
    corrections = int(signals.get("corrections", 0) or 0)
    loops = int(signals.get("tool_loops", 0) or 0)
    achieved = signals.get("achieved")
    if achieved is False:
        return "下次遇到同类任务时，先按这次失败的原因处理，减少重复试错。"
    if corrections:
        return f"下次遇到同类任务时，减少这类来回纠正（本次 {corrections} 次）。"
    if loops:
        return "下次遇到同类工具错误时，更早换一种做法，避免循环。"
    if kind == "script":
        return "把重复步骤固定成可复用的检查，减少手工操作。"
    return "让说明更贴近这个项目的实际做法，减少下次的摸索。"


def _contract_from_session(
    store: ConsumerStore,
    job: dict,
    session: dict,
    workspace: Path,
) -> dict:
    turns = [
        {"text": str(turn.get("text", "")), "corrective": bool(turn.get("corrective"))}
        for turn in session.get("user_turns", [])
        if str(turn.get("text", "")).strip()
    ]
    if not turns:
        raise ConsumerError("INVALID_REQUEST", "这个会话没有可用的用户指令。", "暂时无法分析。")
    project = ProjectManager(store).for_root(Path(session.get("project") or workspace), create=False)
    ref_seed = f"{session.get('host')}\0{session.get('project_id')}\0{session.get('id')}"
    task_ref = "sess-" + hashlib.sha256(ref_seed.encode()).hexdigest()[:32]
    obj = contract.TaskContract.new(
        task_ref=task_ref,
        instruction=turns[0]["text"],
        workspace_sha256=engine_store.tree_hash(workspace) if workspace.is_dir() else "0" * 64,
        domain=str(project.get("domain") or "general"),
        checks=[],
        user_turns=turns,
        expected_deliverables=[],
        source_runtime=session.get("host", "manual"),
        provenance={
            "consumer": True,
            "session_id": session.get("id"),
            "job_id": job.get("id"),
            "host": session.get("host"),
            "project": session.get("project"),
            "events_path": session.get("events_path"),
            "app_version": APP_VERSION,
        },
    )
    payload = obj.to_dict()
    engine_store.atomic_write_json(Path(job["dir"]) / "contract.json", payload)
    return payload


class Analyzer:
    def __init__(self, store: ConsumerStore):
        self.store = store
        self.settings_manager = SettingsManager(store)
        self.projects = ProjectManager(store)
        self.hosts = HostManager(store)
        self.candidates = CandidateManager(store)
        self.budget = ConsumerBudget(store)
        self.attention = AttentionManager(store)
        self.providers = ProvidersManager(store)

    # --- triage ---------------------------------------------------------------
    def triage(self, session: dict, settings: dict, project: dict, *, manual: bool = False) -> dict:
        # Pause/vacation is an explicit user boundary: even a manual request must wait for
        # the pause to be lifted. Other auto-learning switches can be overridden by an
        # explicit one-session request after the send preview.
        if self.attention.is_paused():
            return {"decision": "noop", "reason": "paused"}
        if not settings.get("enabled", True) and not manual:
            return {"decision": "noop", "reason": "disabled"}
        if project and not project.get("enabled", True) and not manual:
            return {"decision": "noop", "reason": "project_disabled"}
        if project and project.get("sensitive") and not manual:
            return {"decision": "noop", "reason": "sensitive_project"}
        if settings.get("trust_level") == "observer" and not manual:
            return {"decision": "noop", "reason": "observer"}
        signals = session.get("signals") or {}
        if signals.get("unsupported"):
            return {"decision": "noop", "reason": "unsupported_events"}
        turns = session.get("user_turns") or []
        if not turns:
            return {"decision": "noop", "reason": "no_user_instruction"}
        depth = settings.get("analysis", {}).get("depth", "adaptive")
        if depth == "light" and not manual:
            return {"decision": "noop", "reason": "light_depth"}
        corrections = int(signals.get("corrections", 0) or 0)
        loops = int(signals.get("tool_loops", 0) or 0)
        achieved = signals.get("achieved")
        delivered = signals.get("delivered")
        failures = signals.get("failures") or []
        if depth != "full" and not manual:
            # A smooth session is a noop only when the runtime explicitly reported success.
            # An unknown/absent completion signal (achieved is None) is a signal, per C.4.4.
            if (
                achieved is True
                and corrections == 0
                and loops == 0
                and delivered is not False
                and not failures
            ):
                return {"decision": "noop", "reason": "no_signal"}
        return {
            "decision": "analyze",
            "reason": "manual" if manual else "signal",
            "signals": signals,
        }

    # --- main -----------------------------------------------------------------
    def run(self, job: dict, session: dict, *, should_stop=None, force: bool = False) -> dict:
        settings = self.settings_manager.load()
        project = self.store.read_record("projects", str(session.get("project_id"))) or {}
        effective = effective_settings(settings, project)
        manual = bool(force)
        triage = self.triage(session, effective, project, manual=manual)
        if triage["decision"] == "noop":
            self.store.append_log("job_noop", job_id=job.get("id"), reason=triage["reason"])
            return {"state": "noop", "reason": triage["reason"], "cost_usd": 0.0}
        workspace = Path(str(session.get("workspace") or ""))
        source = Path(str(session.get("source_workspace") or ""))
        globs = list(effective.get("privacy", {}).get("excluded_globs", []) or [])
        globs.extend(project.get("excluded_globs", []) or [])
        if not workspace.is_dir() or not any(workspace.iterdir()):
            if source.is_dir():
                workspace.parent.mkdir(parents=True, exist_ok=True)
                copy_context(source, workspace, globs=globs)
            else:
                return {"state": "noop", "reason": "no_workspace", "cost_usd": 0.0}
        if should_stop and should_stop():
            return {"state": "paused", "reason": "host_exit", "cost_usd": 0.0}
        self._checkpoint(job, stage="task")
        _contract_from_session(self.store, job, session, workspace)
        host = str(session.get("host"))
        project_root = str(session.get("project") or workspace)
        actual_tree = self.hosts.read_harness_tree(host, project_root=project_root, include_base=False)
        # Only the visible subset is sent to the proposer; the full allowed surface is kept
        # for the base hash and for applying the candidate, so excluded files never make a
        # candidate look stale.
        seed_tree = {}
        for rel, text in actual_tree.items():
            if excluded(rel, globs):
                continue
            _cleaned, labels = sanitize.redact_text(text)
            if labels:
                # Do not send a secret-bearing harness file at all: a redacted placeholder
                # could be written back as if it were real content.
                self.store.append_log("proposal_context_secret_file_skipped", rel=rel)
                continue
            seed_tree[rel] = text
        instruction = {"dsh": "AGENTS.md", "claude-code": "CLAUDE.md", "codex": "AGENTS.md"}[host]
        if instruction not in seed_tree:
            base = self.hosts.read_harness_tree(host, project_root=project_root, include_base=True)
            if instruction in base:
                _cleaned, labels = sanitize.redact_text(base[instruction])
                if not labels:
                    seed_tree[instruction] = base[instruction]
        job_dir = Path(job["dir"])
        seed_dir = job_dir / "seed"
        if seed_dir.exists():
            shutil.rmtree(seed_dir)
        seed_dir.mkdir(parents=True, exist_ok=True)
        for rel, text in seed_tree.items():
            target = seed_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            engine_store.atomic_write_text(target, text)
        preview = preview_payload(self.store, session, effective)
        engine_store.atomic_write_json(job_dir / "preview.json", preview)
        if effective.get("budget", {}).get("confirm_before_analysis") and not manual:
            return {"state": "needs_confirm", "reason": "confirm_before_analysis", "preview": preview, "cost_usd": 0.0}
        clients = self.providers.clients_for_role(effective, "proposer")
        provider = clients[0].provider
        context_chars = sum(len(text) for text in seed_tree.values()) + sum(
            len(str(turn.get("text", ""))) for turn in session.get("user_turns", [])
        ) + len(str(session.get("trajectory_summary", "")))
        estimate = estimate_analysis_usd(provider, context_chars=context_chars, calls=9)
        if estimate is None:
            raise ConsumerError(
                "BUDGET_UNKNOWN_PRICE",
                "这个模型的价格还没有确认，harness2-morphd 不会按 0 计费。",
                "在模型页填写估算价格并确认。",
            )
        budget_cfg = effective.get("budget", {})
        self.budget.precheck(
            estimate,
            monthly_limit=float(budget_cfg.get("monthly_usd", 0)),
            session_limit=float(budget_cfg.get("per_session_usd", 0)),
            job_limit=float(budget_cfg.get("per_job_usd", 0)),
            session_id=str(session.get("id")),
            job_id=str(job.get("id")),
            alert_ratio=float(budget_cfg.get("alert_ratio", 0.8)),
        )
        reservation = self.budget.reserve(
            estimate,
            kind="analysis",
            label=provider.get("model", ""),
            session_id=str(session.get("id")),
            job_id=str(job.get("id")),
            project_id=str(session.get("project_id")),
        )
        self._checkpoint(job, stage="propose", estimate_usd=estimate, reservation=reservation)

        def check_budget() -> None:
            after = self.budget.snapshot(
                monthly_limit=float(budget_cfg.get("monthly_usd", 0)),
                session_limit=float(budget_cfg.get("per_session_usd", 0)),
                job_limit=float(budget_cfg.get("per_job_usd", 0)),
                session_id=str(session.get("id")),
                job_id=str(job.get("id")),
            )
            if (
                after.blocked
                or after.monthly_spent >= after.monthly_limit
                or after.session_spent >= after.session_limit
                or after.job_spent >= after.job_limit
            ):
                raise ConsumerError(
                    "BUDGET_EXCEEDED",
                    "本次分析达到预算上限，已停止后续模型调用。",
                    "调高预算，或选择更便宜的模型。",
                )

        fallback_used = False
        model_used = f"{provider.get('id')}/{provider.get('model')}"
        checkpoint_cost = float((job.get("checkpoint") or {}).get("cost_usd", 0.0) or 0.0)

        def charge(completion, phase: str) -> None:
            nonlocal fallback_used, model_used, checkpoint_cost
            model_used = f"{completion.provider_id}/{completion.model}"
            if getattr(completion, "fallback_used", False):
                fallback_used = True
                self.store.append_log(
                    "provider_fallback_used",
                    job_id=job.get("id"),
                    provider=completion.provider_id,
                    model=completion.model,
                    phase=phase,
                )
            usage = completion.usage or {}
            cost = completion.cost_usd
            estimated = bool(completion.cost_estimated)
            if not usage.get("present"):
                fallback = estimate_analysis_usd(provider, context_chars=context_chars, calls=1)
                cost = fallback if fallback is not None else 0.25
                estimated = True
            self.budget.charge(
                cost,
                kind="analysis",
                label=f"{completion.provider_id}/{completion.model}",
                reservation=reservation,
                session_id=str(session.get("id")),
                job_id=str(job.get("id")),
                project_id=str(session.get("project_id")),
                estimated=estimated,
                usage=usage,
            )
            # The next call is blocked by ``check_budget`` before it is made; the current
            # response is still allowed to finish so its cost is not wasted.
            checkpoint_cost += float(cost or 0.0)
            self._checkpoint(
                job,
                stage="propose",
                cost_usd=round(checkpoint_cost, 8),
                phase=phase,
            )

        proposer = BYOKProposer(
            store=self.store,
            managers=self.providers,
            clients=clients,
            work_root=job_dir / "proposals",
            charge=charge,
            precall=lambda phase: check_budget(),
            max_rounds=8,
            should_stop=should_stop,
        )
        mission = self._mission(session, effective, project)
        stopped = False
        try:
            result = proposer.call(
                mission,
                seed_dir,
                "consumer",
                system=(
                    "You are morphd's recursive harness improver. You may edit only the "
                    "temporary harness. Never modify permissions, sandbox, approvals, hooks, "
                    "settings, MCP configuration or credentials. Follow the user's change "
                    "appetite. Keep the change minimal and testable."
                ),
            )
        except ConsumerError as exc:
            self.budget.release(reservation, reason="proposal_failed")
            if exc.code == "BUDGET_EXCEEDED":
                raise
            raise
        except BaseException:
            self.budget.release(reservation, reason="proposal_failed")
            raise
        finally:
            stopped = bool(should_stop and should_stop())
        self._checkpoint(job, stage="validate")
        if stopped:
            self.budget.release(reservation, reason="host_exit")
            return {"state": "paused", "reason": "host_exit", "cost_usd": result.get("cost_usd", 0.0)}
        if not result.get("ok"):
            self.budget.release(reservation, reason="no_candidate")
            self.store.append_log(
                "proposal_rejected", job_id=job.get("id"), reason=result.get("error", "no_changes")
            )
            return {
                "state": "noop",
                "reason": result.get("error") or "no_changes",
                "cost_usd": result.get("cost_usd", 0.0),
                "summary": result.get("summary", ""),
            }
        seed_hashes = {rel: engine_store.sha256_text(text) for rel, text in seed_tree.items()}
        result_tree = result.get("tree") or read_tree(Path(result["workspace"]))
        deleted = sorted(set(seed_tree) - set(result_tree))
        if deleted:
            self.budget.release(reservation, reason="deletion_not_allowed")
            self.store.append_log(
                "proposal_rejected", job_id=job.get("id"), reason="deletion_not_allowed", files=deleted
            )
            return {
                "state": "noop",
                "reason": "deletion_not_allowed",
                "detail": deleted,
                "cost_usd": result.get("cost_usd", 0.0),
            }
        changed = [
            rel
            for rel, text in result_tree.items()
            if seed_hashes.get(rel) != engine_store.sha256_text(text)
        ]
        # A file missing from the result tree is treated as unchanged (deletion is not a surface).
        proposed = {rel: result_tree[rel] for rel in changed}
        candidate_tree = {**actual_tree, **proposed}
        appetite = effective.get("change_appetite", "conservative")
        check = check_candidate(
            runtime=host,
            base_tree=actual_tree,
            candidate_tree=candidate_tree,
            appetite_name=appetite,
        )
        if not check.ok:
            self.budget.release(reservation, reason="unsafe")
            self.store.append_log(
                "candidate_rejected", job_id=job.get("id"), violations=check.violations[:10]
            )
            return {
                "state": "noop",
                "reason": "unsafe_or_too_large",
                "detail": check.violations,
                "cost_usd": result.get("cost_usd", 0.0),
            }
        candidate_id = new_id("c")
        diff_path = self.store.home / "diffs" / f"{candidate_id}.diff"
        engine_store.atomic_write_text(diff_path, check.diff)
        signals = session.get("signals") or {}
        confidence = candidate_confidence(check.kind, check.lines, signals)
        summary = str(result.get("summary") or "").strip()[:1200] or "更新了项目的 harness 说明。"
        presentation = self.attention.presentation(
            settings=effective,
            project_id=str(session.get("project_id")),
            confidence=confidence,
            kind=check.kind,
        )
        presented = bool(presentation.get("present"))
        if presented:
            self.attention.claim_presentation(str(session.get("project_id")))
        candidate = {
            "version": 1,
            "id": candidate_id,
            "job_id": job.get("id"),
            "session_id": session.get("id"),
            "host": host,
            "project_id": session.get("project_id"),
            "project": session.get("project"),
            "base_hash": engine_store.payload_hash(
                {rel: engine_store.sha256_text(text) for rel, text in sorted(actual_tree.items())}
            ),
            "candidate_hash": engine_store.payload_hash(
                {rel: engine_store.sha256_text(text) for rel, text in sorted(candidate_tree.items())}
            ),
            "files_changed": check.changed,
            "added_files": sorted(set(candidate_tree) - set(actual_tree)),
            "kind": check.kind,
            "diff_path": str(diff_path),
            "summary": summary,
            "expected_effect": _expected_effect(check.kind, signals),
            "risk": risk_statement(check),
            "cost_usd": round(float(result.get("cost_usd", 0.0) or 0.0), 8),
            "confidence": confidence,
            "model": f"{provider.get('id')}/{provider.get('model')}",
            "model_used": model_used,
            "fallback_used": fallback_used,
            "safety": check.to_dict(),
            "state": "needs_apply",
            "presented": presented,
            "presentation_reason": presentation.get("reason", ""),
            "present_after": _present_after(session, effective),
            "created_at": _now(),
            "applied_at": None,
            "reverted_at": None,
            "trial": {"n": 0, "baseline": {}, "observed": []},
            "feedback": [],
            "base_tree": actual_tree,
            "candidate_tree": candidate_tree,
            "source": {
                "session_id": session.get("id"),
                "ended_at": session.get("ended_at"),
                "signals": signals,
            },
            "app_version": APP_VERSION,
        }
        self.store.write_record("candidates", candidate_id, candidate)
        self._checkpoint(job, stage="needs_apply", candidate_id=candidate_id)
        state = "needs_apply"
        if self._should_auto_apply(effective, check, host):
            # C.4.4: with no independent online baseline for this host+project we never
            # auto-apply, even when auto-apply is on; relative improvement cannot be judged
            # against only the session that just failed and produced this candidate.
            source_session_id = str((candidate.get("source") or {}).get("session_id") or "")
            prior = self.candidates.baseline(candidate, exclude_session_id=source_session_id or None)
            if prior.get("n"):
                try:
                    candidate = self.candidates.apply(candidate_id, confirm=True, automatic=True)
                    state = candidate.get("state", "applied")
                    if state == "applied":
                        self.store.append_log(
                            "candidate_auto_applied", candidate_id=candidate_id, host=host
                        )
                except ConsumerError as exc:
                    self.store.append_log(
                        "auto_apply_failed", candidate_id=candidate_id, code=exc.code, message=exc.message
                    )
            else:
                self.store.append_log("auto_apply_skipped_no_baseline", candidate_id=candidate_id)
        self.budget.release(reservation, reason="candidate_ready")
        return {
            "state": state,
            "candidate_id": candidate_id,
            "cost_usd": candidate.get("cost_usd", 0.0),
            "presented": presented,
            "presentation_reason": presentation.get("reason", ""),
        }

    def preview(self, session: dict) -> dict:
        settings = self.settings_manager.load()
        project = self.store.read_record("projects", str(session.get("project_id"))) or {}
        return preview_payload(self.store, session, effective_settings(settings, project))

    def _mission(self, session: dict, settings: dict, project: dict) -> str:
        signals = session.get("signals") or {}
        turns = session.get("user_turns") or []
        turn_text = "\n".join(
            f"U{index + 1}{' (correction)' if turn.get('corrective') else ''}: {_clips(turn.get('text', ''))}"
            for index, turn in enumerate(turns[:12])
        )
        trajectory = _clips(session.get("trajectory_summary", ""), 6000)
        appetite = APPETITES.get(settings.get("change_appetite", "conservative"), APPETITES["conservative"])
        surfaces = {
            "conservative": "only the existing instruction file (AGENTS.md/CLAUDE.md); no new scripts",
            "balanced": "the instruction file, skills, and existing scripts; at most 3 files/100 lines",
            "bold": "instruction, skills, scripts and declared agent files; at most 10 files/300 lines",
        }[appetite.name]
        return (
            "Improve the project's agent harness so the next similar session goes better.\n"
            f"Host: {session.get('host')} | Project: {Path(str(session.get('project') or 'project')).name} | Domain: "
            f"{project.get('domain', 'general')}\n"
            f"Session outcome: {signals.get('achieved')} | corrections={signals.get('corrections', 0)} | "
            f"tool_loops={signals.get('tool_loops', 0)} | end_reason={session.get('end_reason')}\n\n"
            f"User turns from the session:\n{turn_text}\n\n"
            f"Session trajectory (redacted, truncated):\n{trajectory}\n\n"
            f"Allowed change appetite: {surfaces}.\n"
            "Do not add secrets, network calls, subprocesses, dynamic execution, permission "
            "changes, sandbox changes, hooks, MCP or settings edits. Prefer one concrete, "
            "small instruction that addresses the observed failure. If no safe change would "
            "help, leave the harness unchanged and explain why."
        )

    def _should_auto_apply(self, settings: dict, check, host: str) -> bool:
        if not settings.get("auto_apply"):
            return False
        if settings.get("trust_level") not in ("assistant", "autopilot"):
            return False
        if settings.get("trust_level") == "assistant" and (
            settings.get("change_appetite") != "conservative" or check.kind != "instruction"
        ):
            return False
        window = settings.get("auto_apply_window", "any")
        if window == "quiet":
            quiet = settings.get("cadence", {}).get("quiet_hours")
            if not AttentionManager._in_quiet_hours(quiet, datetime.now().astimezone()):
                return False
        elif window == "custom":
            custom = [
                settings.get("cadence", {}).get("auto_apply_window_start", "22:00"),
                settings.get("cadence", {}).get("auto_apply_window_end", "08:00"),
            ]
            if not AttentionManager._in_quiet_hours(custom, datetime.now().astimezone()):
                return False
        return True

    def _checkpoint(self, job: dict, **fields) -> None:
        checkpoint = dict(job.get("checkpoint") or {})
        checkpoint.update(fields)
        checkpoint["updated_at"] = _now()
        job["checkpoint"] = checkpoint
        job["updated_at"] = _now()
        self.store.write_record("jobs", str(job["id"]), job)
