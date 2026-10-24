"""Candidate lifecycle: apply, revert, trial and automatic rollback."""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import store as engine_store
from .attention import AttentionManager
from .budget import ConsumerBudget
from .errors import ConsumerError
from .hosts import HostManager
from .policy import check_candidate
from .settings import ProjectManager, SettingsManager, effective_settings
from .snapshots import SnapshotManager, tree_digest
from .store import ConsumerStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _median(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


class CandidateManager:
    def __init__(self, store: ConsumerStore):
        self.store = store
        self.hosts = HostManager(store)
        self.snapshots = SnapshotManager(store)
        self.settings_manager = SettingsManager(store)
        self.attention = AttentionManager(store)
        self.budget = ConsumerBudget(store)

    def get(self, candidate_id: str) -> dict:
        record = self.store.read_record("candidates", candidate_id)
        if not isinstance(record, dict):
            raise ConsumerError("CANDIDATE_NOT_FOUND", "这条建议不存在。", "刷新历史页。")
        return record

    def list(self, *, state: str | None = None, project_id: str | None = None) -> list[dict]:
        records = self.store.list_records("candidates")
        return [
            item
            for item in records
            if (state is None or item.get("state") == state)
            and (project_id is None or item.get("project_id") == project_id)
        ]

    def current_tree(self, candidate: dict) -> dict[str, str]:
        return self.hosts.read_harness_tree(
            candidate["host"], project_root=candidate.get("project")
        )

    def is_stale(self, candidate: dict) -> bool:
        current = self.current_tree(candidate)
        return tree_digest(current) != candidate.get("base_hash")

    def apply(self, candidate_id: str, *, confirm: bool = False, automatic: bool = False) -> dict:
        if not confirm:
            raise ConsumerError("INVALID_REQUEST", "应用前需要确认。", "在建议卡片上点击“应用”。")
        candidate = self.get(candidate_id)
        if candidate.get("state") not in ("needs_apply",):
            raise ConsumerError(
                "INVALID_STATE",
                f"这条建议当前状态是 {candidate.get('state')}，不能应用。",
                "刷新页面后重试。",
            )
        settings = self.settings_manager.load()
        project = self.store.read_record("projects", str(candidate.get("project_id"))) or {}
        effective = effective_settings(settings, project)
        if automatic:
            if not effective.get("auto_apply") or effective.get("trust_level") not in ("assistant", "autopilot"):
                raise ConsumerError("FORBIDDEN", "自动应用未开启。", "在设置中显式开启。")
            if not effective.get("auto_rollback"):
                raise ConsumerError("FORBIDDEN", "自动应用要求自动回滚保持开启。", "先开启自动回滚。")
        current = self.current_tree(candidate)
        digest = tree_digest(current)
        base_tree = self._load_tree(candidate)
        if digest != candidate.get("base_hash"):
            # C.4.4: distinguish a manual edit of a file this candidate would overwrite
            # (APPLY_CONFLICT, keep the candidate so nothing is lost) from unrelated harness
            # drift that merely invalidates the candidate (CANDIDATE_STALE).
            conflict = []
            if isinstance(candidate.get("base_tree"), dict):
                for rel in candidate.get("files_changed", []) or []:
                    if (current.get(rel) or "") != (base_tree.get(rel) or ""):
                        conflict.append(rel)
            if conflict:
                raise ConsumerError(
                    "APPLY_CONFLICT",
                    "你要应用的文件已被手动修改，直接应用会覆盖这些改动。",
                    "先处理冲突文件，或重新分析生成新的建议。",
                    details={"conflicts": conflict},
                )
            self._set_state(candidate, "reverted", error="stale")
            raise ConsumerError(
                "CANDIDATE_STALE",
                "harness 已经变化，这条建议基于旧版本，不能直接应用。",
                "重新分析，或查看历史对比。",
            )
        check = check_candidate(
            runtime=candidate["host"],
            base_tree=base_tree,
            candidate_tree=candidate.get("candidate_tree") or {},
            appetite_name=effective.get("change_appetite", "conservative"),
        )
        if not check.ok:
            raise ConsumerError(
                "CANDIDATE_UNSAFE",
                "这条建议在重新校验时被拒绝。",
                "查看安全原因，或重新分析。",
                details={"violations": check.violations, "findings": check.findings},
            )
        snapshot = self.snapshots.create(
            kind=candidate["host"],
            project_root=candidate["project"],
            reason="apply",
            candidate_id=candidate_id,
        )
        changed = {
            rel: (candidate.get("candidate_tree") or {})[rel]
            for rel in candidate.get("files_changed", []) or []
            if rel in (candidate.get("candidate_tree") or {})
        }
        try:
            self.hosts.apply_tree(
                candidate["host"],
                changed,
                project_root=candidate.get("project"),
            )
        except Exception:
            self.snapshots.restore(snapshot)
            raise
        applied_hash = tree_digest(self.current_tree(candidate))
        baseline = self.baseline(candidate)
        candidate.update(
            {
                "state": "applied",
                "applied_at": _now(),
                "snapshot_id": snapshot["id"],
                "applied_hash": applied_hash,
                "applied_files": {
                    rel: engine_store.sha256_text(text) for rel, text in changed.items()
                },
                "base_tree": base_tree,
                "safety": check.to_dict(),
                "automatic": bool(automatic),
                "trial": {
                    "n": 0,
                    "baseline": baseline,
                    "observed": [],
                    "started_at": _now(),
                },
            }
        )
        self.store.write_record("candidates", candidate_id, candidate)
        self._sync_job(candidate)
        self.store.append_log(
            "candidate_applied",
            candidate_id=candidate_id,
            host=candidate.get("host"),
            automatic=bool(automatic),
            files=candidate.get("files_changed"),
        )
        return candidate

    def revert(self, candidate_id: str, *, confirm: bool = False, reason: str = "manual") -> dict:
        if not confirm:
            raise ConsumerError("INVALID_REQUEST", "撤销前需要确认。", "在历史页点击“撤销”。")
        candidate = self.get(candidate_id)
        if candidate.get("state") not in ("applied", "trial", "kept"):
            raise ConsumerError("INVALID_STATE", "这条建议没有处于可撤销状态。", "刷新页面。")
        settings = self.settings_manager.load()
        undo_days = int(settings.get("undo_days", 7) or 7)
        applied = _parse(candidate.get("applied_at"))
        automatic_rollback = reason.startswith(("trial:", "feedback:", "restore_original"))
        if (
            applied
            and not automatic_rollback
            and datetime.now(timezone.utc) > applied + timedelta(days=undo_days)
        ):
            raise ConsumerError(
                "CANDIDATE_STALE",
                f"已超过 {undo_days} 天撤销窗口。",
                "使用“恢复原始 harness”或手动处理。",
            )
        snapshot_id = candidate.get("snapshot_id")
        if not snapshot_id:
            raise ConsumerError("NOT_FOUND", "没有找到这条建议的快照。", "无法安全撤销。")
        snapshot = self.snapshots.get(snapshot_id)
        applied_files = dict(candidate.get("applied_files") or {})
        for rel, expected in applied_files.items():
            target = self.hosts.target_for(candidate["host"], rel, project_root=candidate.get("project"))
            if not target.is_file():
                continue
            current = engine_store.sha256_text(target.read_text(encoding="utf-8", errors="replace"))
            if current != expected:
                raise ConsumerError(
                    "APPLY_CONFLICT",
                    f"{rel} 在应用后被手动修改过，撤销会覆盖你的改动。",
                    "先备份或提交该文件，再撤销。",
                )
        # Restore only the files this candidate touched. Restoring every file captured by
        # the snapshot would silently overwrite unrelated user edits made after apply.
        target_rels = set(applied_files) | set(candidate.get("added_files", []) or [])
        filtered = {
            **snapshot,
            "entries": [
                entry
                for entry in snapshot.get("entries", [])
                if str(entry.get("rel")) in target_rels
            ],
        }
        self.snapshots.restore(filtered)
        # Remove files that the candidate created, but only if they still match what we wrote.
        for rel in candidate.get("added_files", []) or []:
            target = self.hosts.target_for(candidate["host"], rel, project_root=candidate.get("project"))
            expected = (candidate.get("candidate_tree") or {}).get(rel)
            if target.is_file() and expected is not None:
                if engine_store.sha256_text(target.read_text(encoding="utf-8", errors="replace")) == engine_store.sha256_text(expected):
                    try:
                        target.unlink()
                    except OSError:
                        pass
        candidate.update(
            {
                "state": "reverted",
                "reverted_at": _now(),
                "revert_reason": reason,
                "trial": {**(candidate.get("trial") or {}), "ended_at": _now()},
            }
        )
        self.store.write_record("candidates", candidate_id, candidate)
        self._sync_job(candidate)
        self.store.append_log("candidate_reverted", candidate_id=candidate_id, reason=reason)
        return candidate

    def feedback(self, candidate_id: str, reason: str, *, note: str = "") -> dict:
        reasons = {
            "useful",
            "not_useful",
            "intrusive",
            "expensive",
            "wrong",
            "already_known",
            "ignore",
            "not_this_kind",
        }
        if reason not in reasons:
            raise ConsumerError("INVALID_REQUEST", "未知的反馈类型。", "选择有用的反馈原因。")
        candidate = self.get(candidate_id)
        feedback = list(candidate.get("feedback") or [])
        feedback.append({"reason": reason, "note": note[:1000], "at": _now()})
        candidate["feedback"] = feedback
        self.store.write_record("candidates", candidate_id, candidate)
        stats = self.attention.record_feedback(
            str(candidate.get("project_id") or ""),
            reason,
            candidate_kind=str(candidate.get("kind") or ""),
        )
        if reason in ("not_useful", "wrong") and candidate.get("state") in ("applied", "trial"):
            candidate = self.revert(candidate_id, confirm=True, reason=f"feedback:{reason}")
        return {"ok": True, "candidate": candidate, "attention": stats}

    # --- trial ---------------------------------------------------------------
    def baseline(self, candidate: dict, *, exclude_session_id: str | None = None) -> dict:
        applied = _parse(candidate.get("applied_at")) or datetime.now(timezone.utc)
        sessions = []
        for session in self.store.list_records("sessions"):
            if session.get("host") != candidate.get("host"):
                continue
            if session.get("project_id") != candidate.get("project_id"):
                continue
            if exclude_session_id and session.get("id") == exclude_session_id:
                continue
            # C.4.4: the baseline uses recent non-incognito, non-paused sessions only.
            if session.get("status") in ("paused", "vacation", "incognito"):
                continue
            ended = _parse(session.get("ended_at"))
            if ended is None or ended > applied:
                continue
            sessions.append(session)
        sessions = sorted(sessions, key=lambda s: s.get("ended_at", ""))[-5:]
        signals = [s.get("signals") or {} for s in sessions]
        return {
            "n": len(sessions),
            "achieved_rate": (
                sum(1 for s in signals if s.get("achieved") is True) / len(signals) if signals else None
            ),
            "corrections": _median([float(s.get("corrections", 0) or 0) for s in signals]),
            "tool_loops": _median([float(s.get("tool_loops", 0) or 0) for s in signals]),
            "turns": _median([float(s.get("turns", 0) or 0) for s in signals]),
            "session_ids": [s.get("id") for s in sessions],
        }

    def observe_session(self, session: dict) -> list[dict]:
        """Update every open trial for matching host/project after a session ends."""
        results = []
        for candidate in self.list(state="applied") + self.list(state="trial"):
            if candidate.get("host") != session.get("host"):
                continue
            if candidate.get("project_id") != session.get("project_id"):
                continue
            trial = candidate.get("trial") or {}
            observed = list(trial.get("observed") or [])
            if any(item.get("session_id") == session.get("id") for item in observed):
                continue
            observed.append(
                {
                    "session_id": session.get("id"),
                    "ended_at": session.get("ended_at"),
                    "signals": session.get("signals") or {},
                }
            )
            trial["observed"] = observed
            trial["n"] = len(observed)
            candidate["trial"] = trial
            candidate["state"] = "trial"
            self.store.write_record("candidates", str(candidate["id"]), candidate)
            self._sync_job(candidate)
            decision = self._trial_decision(candidate)
            auto_rollback = bool(self.settings_manager.load().get("auto_rollback", True))
            if decision.get("revert") and not auto_rollback and not decision.get("hard"):
                decision["auto_rollback_disabled"] = True
                decision["revert"] = False
            if decision.get("revert"):
                candidate = self.revert(
                    str(candidate["id"]), confirm=True, reason=f"trial:{decision['reason']}"
                )
                decision["candidate"] = candidate
            elif decision.get("keep"):
                candidate["state"] = "kept"
                candidate["kept_at"] = _now()
                self.store.write_record("candidates", str(candidate["id"]), candidate)
                self._sync_job(candidate)
                decision["candidate"] = candidate
            results.append(decision)
        return results

    def _trial_decision(self, candidate: dict) -> dict:
        trial = candidate.get("trial") or {}
        observed = trial.get("observed") or []
        baseline = trial.get("baseline") or {}
        settings = self.settings_manager.load()
        trial_sessions = int(settings.get("trial_sessions", 3) or 3)
        if not observed:
            return {"candidate_id": candidate.get("id"), "revert": False, "keep": False}
        last_two = observed[-2:]
        corrections = [int((o.get("signals") or {}).get("corrections", 0) or 0) for o in last_two]
        loops = [int((o.get("signals") or {}).get("tool_loops", 0) or 0) for o in last_two]
        base_corr = baseline.get("corrections")
        base_loops = baseline.get("tool_loops")
        for item in observed:
            signals = item.get("signals") or {}
            if signals.get("security_failed") is True:
                return {
                    "candidate_id": candidate.get("id"),
                    "revert": True,
                    "hard": True,
                    "reason": "security_failed",
                }
            if signals.get("delivered") is False:
                return {
                    "candidate_id": candidate.get("id"),
                    "revert": True,
                    "hard": True,
                    "reason": "delivered_false",
                }
            if signals.get("crash") is True:
                return {
                    "candidate_id": candidate.get("id"),
                    "revert": True,
                    "hard": True,
                    "reason": "host_crash",
                }
            if signals.get("user_reverted") is True:
                return {
                    "candidate_id": candidate.get("id"),
                    "revert": True,
                    "hard": True,
                    "reason": "user_reverted",
                }
        if (
            base_corr is not None
            and len(last_two) == 2
            and all(value >= base_corr + 1 for value in corrections)
        ):
            return {
                "candidate_id": candidate.get("id"),
                "revert": True,
                "hard": True,
                "reason": "corrections_regressed",
            }
        if (
            base_loops is not None
            and len(last_two) == 2
            and all(value > base_loops for value in loops)
        ):
            return {
                "candidate_id": candidate.get("id"),
                "revert": True,
                "hard": True,
                "reason": "tool_loops_regressed",
            }
        applied_at = _parse(candidate.get("applied_at"))
        undo_days = int(settings.get("undo_days", 7) or 7)
        if (
            applied_at is not None
            and len(observed) < trial_sessions
            and datetime.now(timezone.utc) > applied_at + timedelta(days=undo_days)
        ):
            return {
                "candidate_id": candidate.get("id"),
                "revert": True,
                "hard": True,
                "reason": "trial_window_expired",
            }
        if int(baseline.get("n", 0) or 0) == 0:
            # No baseline: only hard rollback signals apply. A neutral trial is not evidence
            # of regression, so keep the candidate instead of guessing.
            if len(observed) >= trial_sessions:
                return {"candidate_id": candidate.get("id"), "revert": False, "keep": True, "reason": "no_baseline"}
            return {"candidate_id": candidate.get("id"), "revert": False, "keep": False}
        if len(observed) >= trial_sessions:
            improvements = 0
            regressions = 0
            base_ach = baseline.get("achieved_rate")
            achieved = [
                1.0 if (o.get("signals") or {}).get("achieved") is True else 0.0
                for o in observed
                if (o.get("signals") or {}).get("achieved") is not None
            ]
            if base_ach is not None and achieved:
                current = sum(achieved) / len(achieved)
                improvements += int(current > base_ach)
                regressions += int(current < base_ach)
            if base_corr is not None:
                current_corr = _median([float((o.get("signals") or {}).get("corrections", 0) or 0) for o in observed])
                if current_corr is not None:
                    improvements += int(current_corr < base_corr)
                    regressions += int(current_corr > base_corr)
            if base_loops is not None:
                current_loops = _median([float((o.get("signals") or {}).get("tool_loops", 0) or 0) for o in observed])
                if current_loops is not None:
                    improvements += int(current_loops < base_loops)
                    regressions += int(current_loops > base_loops)
            # The PRD requires at least one improving signal and no regression to keep a
            # candidate; a neutral trial is not evidence of improvement.
            if regressions == 0 and improvements >= 1:
                return {"candidate_id": candidate.get("id"), "revert": False, "keep": True, "reason": "improved"}
            return {"candidate_id": candidate.get("id"), "revert": True, "reason": "no_improvement"}
        return {"candidate_id": candidate.get("id"), "revert": False, "keep": False}

    def restore_original(self, *, host: str, project_root: str, confirm: bool = False) -> dict:
        if not confirm:
            raise ConsumerError("INVALID_REQUEST", "恢复原始 harness 前需要确认。", "在设置中点击确认。")
        project = ProjectManager(self.store).for_root(Path(project_root))
        manifest = self.snapshots.first_for_project(project["id"])
        if manifest is None:
            raise ConsumerError("NOT_FOUND", "还没有 morphd 改动前的快照。", "无需恢复，或手动检查 harness。")
        self.snapshots.restore(manifest)
        for candidate in self.list(state="kept") + self.list(state="trial") + self.list(state="applied"):
            if candidate.get("project_id") != project["id"]:
                continue
            for rel in candidate.get("added_files", []) or []:
                target = self.hosts.target_for(host, rel, project_root=project_root)
                expected = (candidate.get("candidate_tree") or {}).get(rel)
                if target.is_file() and expected and target.read_text(encoding="utf-8", errors="replace") == expected:
                    try:
                        target.unlink()
                    except OSError:
                        pass
            candidate["state"] = "reverted"
            candidate["reverted_at"] = _now()
            candidate["revert_reason"] = "restore_original"
            self.store.write_record("candidates", str(candidate["id"]), candidate)
        self.store.append_log("restore_original", host=host, project_id=project["id"])
        return {"ok": True, "snapshot_id": manifest["id"], "project_id": project["id"]}

    # --- helpers -------------------------------------------------------------
    def _sync_job(self, candidate: dict) -> None:
        job_id = candidate.get("job_id")
        if not job_id:
            return
        job = self.store.read_record("jobs", str(job_id))
        if not isinstance(job, dict):
            return
        state = str(candidate.get("state") or "")
        if state in ("applied", "trial", "kept", "reverted", "needs_apply"):
            job["state"] = state
            job["candidate_id"] = candidate.get("id")
            job["updated_at"] = _now()
            self.store.write_record("jobs", str(job_id), job)

    def _set_state(self, candidate: dict, state: str, *, error: str = "") -> None:
        candidate["state"] = state
        if error:
            candidate["error"] = error
        self.store.write_record("candidates", str(candidate["id"]), candidate)

    def _load_tree(self, candidate: dict) -> dict[str, str]:
        tree = candidate.get("base_tree")
        if isinstance(tree, dict):
            return {str(k): str(v) for k, v in tree.items()}
        return self.current_tree(candidate)
