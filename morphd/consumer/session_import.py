"""Normalise host sessions into the consumer SessionContext and SessionRecord."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .. import sanitize, tasksource
from .. import store as engine_store
from .errors import ConsumerError
from .settings import ProjectManager
from .store import ConsumerStore

END_REASONS = {
    "completed",
    "error",
    "blocked",
    "max-tokens",
    "aborted",
    "interrupted",
    "forked",
    "unknown",
}
CORRECTION_CUES = tasksource.CORRECTION_CUES
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}[T ][0-9:.+-]+Z?)")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(value) -> str:
    if isinstance(value, (int, float)):
        try:
            number = float(value)
            if number > 10_000_000_000:
                number /= 1000.0
            return datetime.fromtimestamp(number, timezone.utc).isoformat(timespec="seconds")
        except (OSError, ValueError, OverflowError):
            return ""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(
            timespec="seconds"
        )
    except ValueError:
        match = _TS_RE.match(text)
        if match:
            try:
                return datetime.fromisoformat(match.group(1).replace("Z", "+00:00")).astimezone(
                    timezone.utc
                ).isoformat(timespec="seconds")
            except ValueError:
                return ""
        return ""


def _clip(text: str, limit: int = 50_000) -> str:
    text = str(text or "").replace("\x00", "")
    return text if len(text) <= limit else text[:limit] + f"\n…(+{len(text) - limit} chars)"


def _text_blocks(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            if isinstance(block.get("text"), str):
                parts.append(block["text"])
    return "\n".join(part for part in parts if part).strip()


def _is_corrective(text: str, last_failed: bool) -> bool:
    low = text.strip().lower()
    return last_failed or any(cue in low for cue in CORRECTION_CUES)


def _read_jsonl(path: Path) -> list[dict]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise ConsumerError("INVALID_REQUEST", f"无法读取会话文件：{exc}", "检查会话路径。") from exc
    if raw[:4] == b"\x28\xb5\x2f\xfd":
        try:
            import zstandard

            with zstandard.ZstdDecompressor().stream_reader(raw) as reader:
                raw = reader.read()
        except Exception as exc:
            raise ConsumerError("INVALID_REQUEST", f"无法解压会话文件：{exc}", "检查会话文件。") from exc
    rows = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


@dataclass
class SessionContext:
    session_id: str
    host: str
    project_root: str
    workspace: str
    events_path: str
    started_at: str = ""
    ended_at: str = ""
    end_reason: str = "unknown"
    user_turns: list[dict] = field(default_factory=list)
    approvals: dict = field(default_factory=lambda: {"allowed": 0, "rejected": 0})
    signals: dict = field(default_factory=dict)
    trajectory_summary: str = ""
    usage: dict = field(default_factory=dict)
    cost_usd: float | None = None
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "host": self.host,
            "project_root": self.project_root,
            "workspace": self.workspace,
            "events_path": self.events_path,
            "started_at": self.started_at,
            "ended_at": self.ended_at or _now(),
            "end_reason": self.end_reason if self.end_reason in END_REASONS else "unknown",
            "user_turns": self.user_turns,
            "approvals": self.approvals,
            "signals": self.signals,
            "trajectory_summary": _clip(self.trajectory_summary),
            "usage": self.usage,
            "cost_usd": self.cost_usd,
            "source": self.source,
        }


def context_from_payload(payload: dict) -> SessionContext:
    host = str(payload.get("host") or "")
    if host not in ("dsh", "claude-code", "codex"):
        raise ConsumerError("HOST_UNSUPPORTED", "未知宿主。", "使用 dsh、claude-code 或 codex。")
    turns = []
    for index, turn in enumerate(payload.get("user_turns") or []):
        if isinstance(turn, str):
            text = turn
            corrective = index > 0 and _is_corrective(text, False)
        elif isinstance(turn, dict):
            text = str(turn.get("text", ""))
            corrective = bool(turn.get("corrective"))
        else:
            continue
        if text.strip():
            turns.append({"text": text, "corrective": corrective})
    signals = payload.get("signals") or {}
    return SessionContext(
        session_id=str(payload.get("session_id") or engine_store.sha256_text(_now())[:16]),
        host=host,
        project_root=str(Path(payload.get("project_root") or payload.get("project") or ".").resolve()),
        workspace=str(Path(payload.get("workspace") or payload.get("project_root") or ".").resolve()),
        events_path=str(payload.get("events_path") or ""),
        started_at=_iso(payload.get("started_at")) or _now(),
        ended_at=_iso(payload.get("ended_at")) or _now(),
        end_reason=str(payload.get("end_reason") or "unknown"),
        user_turns=turns,
        approvals=payload.get("approvals") or {"allowed": 0, "rejected": 0},
        signals=signals if isinstance(signals, dict) else {},
        trajectory_summary=str(payload.get("trajectory_summary") or ""),
        usage=payload.get("usage") or {},
        cost_usd=payload.get("cost_usd"),
        source=str(payload.get("source") or "host-event"),
    )


def _dsh_trajectory(events: list[dict]) -> str:
    lines = []
    for event in events:
        etype = event.get("type")
        data = event.get("data") or {}
        if etype == "user/message":
            message = data.get("message") if isinstance(data.get("message"), dict) else data
            text = _text_blocks(message.get("content"))
            if text:
                lines.append(f"[USER] {_clip(text, 4000)}")
        elif etype == "assistant/message":
            message = data.get("message") if isinstance(data.get("message"), dict) else data
            text = _text_blocks(message.get("content"))
            if text:
                lines.append(f"[ASSISTANT] {_clip(text, 4000)}")
        elif etype == "tool/call":
            lines.append(f"[ACTION] {data.get('name', 'tool')} {json.dumps(data.get('input') or data.get('args') or {}, ensure_ascii=False)[:400]}")
        elif etype == "tool/result":
            message = data.get("message") or {}
            prefix = "[OBSERVATION ERROR]" if message.get("isError") else "[OBSERVATION]"
            text = _text_blocks(message.get("content"))
            if text:
                lines.append(f"{prefix} {_clip(text, 2000)}")
        elif etype == "turn/end":
            reason = data.get("reason") or {}
            kind = reason.get("kind") if isinstance(reason, dict) else ""
            if kind:
                lines.append(f"[HARNESS] turn/end {kind}")
    return "\n".join(lines)


def ingest_dsh(
    *,
    events_path: Path,
    workspace: Path,
    project_root: Path | None = None,
    end_reason: str | None = None,
) -> SessionContext:
    finding = tasksource.read_session(Path(events_path))
    header = finding.header or {}
    turns = [{"text": turn.text, "corrective": turn.corrective} for turn in finding.turns]
    reasons = []
    for event in finding.events:
        if event.get("type") == "turn/end":
            reason = (event.get("data") or {}).get("reason") or {}
            kind = reason.get("kind") if isinstance(reason, dict) else None
            if isinstance(kind, str):
                reasons.append(kind)
    reason = end_reason or (reasons[-1] if reasons else "unknown")
    if reason in ("end_turn", "stop", "success"):
        reason = "completed"
    approvals = {"allowed": 0, "rejected": finding.rejected_approvals}
    for event in finding.events:
        if event.get("type") == "approval/decided":
            if (event.get("data") or {}).get("outcome") != "rejected":
                approvals["allowed"] += 1
    achieved = None if not reasons else reason == "completed"
    corrections = sum(1 for turn in turns[1:] if turn["corrective"])
    cwd = Path(finding.cwd) if finding.cwd else Path(workspace)
    mtimes = []
    try:
        mtimes.append(Path(events_path).stat().st_mtime)
    except OSError:
        pass
    started = _iso(header.get("createdAt") or header.get("created_at") or header.get("timestamp"))
    ended = _iso(header.get("updatedAt") or header.get("updated_at"))
    if not ended and mtimes:
        ended = datetime.fromtimestamp(max(mtimes), timezone.utc).isoformat(timespec="seconds")
    usage = {}
    for event in finding.events:
        if event.get("type") == "assistant/message":
            data = event.get("data") or {}
            message = data.get("message") if isinstance(data.get("message"), dict) else data
            item = message.get("usage") or {}
            for key, value in item.items():
                if isinstance(value, (int, float)):
                    usage[key] = usage.get(key, 0) + value
    return SessionContext(
        session_id=finding.session_id,
        host="dsh",
        project_root=str((project_root or cwd).resolve()),
        workspace=str(Path(workspace).resolve()),
        events_path=str(Path(events_path).resolve()),
        started_at=started or ended or _now(),
        ended_at=ended or _now(),
        end_reason=reason if reason in END_REASONS else "unknown",
        user_turns=turns,
        approvals=approvals,
        signals={
            "achieved": achieved,
            "delivered": None,
            "corrections": corrections,
            "tool_loops": finding.tool_error_loops,
            "failures": finding.failures,
        },
        trajectory_summary=_dsh_trajectory(finding.events),
        usage=usage,
        source=f"dsh-session:{Path(events_path).name}",
    )


def _claude_tool_loops(events: list[dict]) -> tuple[int, dict, list[dict]]:
    calls: dict[str, str] = {}
    loops = 0
    streak = 0
    previous = None
    user_turns = []
    last_failed = False
    trajectory = []
    for event in events:
        etype = event.get("type")
        message = event.get("message") or {}
        if etype == "user":
            content = message.get("content")
            if isinstance(content, list) and any(
                isinstance(block, dict) and block.get("type") == "tool_result" for block in content
            ):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    if block.get("is_error"):
                        tool = calls.get(str(block.get("tool_use_id")), "")
                        reason = _text_blocks(block.get("content"))[:200]
                        sig = (tool, reason)
                        streak = streak + 1 if sig == previous else 1
                        previous = sig
                        if streak >= 3:
                            loops += 1
                            streak = 0
                    else:
                        streak = 0
                        previous = None
                continue
            text = _text_blocks(content)
            if text:
                corrective = _is_corrective(text, last_failed) and bool(user_turns)
                user_turns.append({"text": text, "corrective": corrective})
                trajectory.append(f"[USER] {_clip(text, 4000)}")
        elif etype == "assistant":
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    trajectory.append(f"[ASSISTANT] {_clip(block.get('text', ''), 4000)}")
                elif block.get("type") == "tool_use":
                    calls[str(block.get("id"))] = str(block.get("name"))
                    trajectory.append(
                        f"[ACTION] {block.get('name')} {json.dumps(block.get('input') or {}, ensure_ascii=False)[:400]}"
                    )
        elif etype == "result":
            last_failed = bool(event.get("is_error"))
            trajectory.append(f"[HARNESS] result {event.get('subtype', '')}")
    return loops, {"turns": user_turns}, trajectory


def ingest_claude(
    *,
    events_path: Path,
    workspace: Path,
    project_root: Path | None = None,
    end_reason: str | None = None,
) -> SessionContext:
    events = _read_jsonl(Path(events_path))
    session_id = ""
    cwd = ""
    started = ""
    ended = ""
    result = None
    usage = {}
    last_reason = "unknown"
    for event in events:
        session_id = session_id or str(event.get("session_id") or "")
        cwd = cwd or str(event.get("cwd") or "")
        started = started or _iso(event.get("timestamp") or event.get("created_at"))
        ended = _iso(event.get("timestamp") or event.get("created_at")) or ended
        if event.get("type") == "result":
            result = event
            subtype = str(event.get("subtype") or "")
            if event.get("is_error"):
                last_reason = "error"
            elif "max_tokens" in subtype:
                last_reason = "max-tokens"
            elif subtype in ("success", "end_turn", ""):
                last_reason = "completed"
            else:
                last_reason = "unknown"
            raw_usage = event.get("usage") or {}
            usage = {
                "input_tokens": raw_usage.get("input_tokens", 0),
                "output_tokens": raw_usage.get("output_tokens", 0),
                "cached_input_tokens": raw_usage.get("cache_read_input_tokens", 0),
                "cache_write_tokens": raw_usage.get("cache_creation_input_tokens", 0),
            }
    loops, turns_info, trajectory = _claude_tool_loops(events)
    turns = turns_info["turns"]
    corrections = sum(1 for turn in turns[1:] if turn.get("corrective"))
    # A SessionEnd hook supplies a completion hint, but a parsed result event is more
    # specific and must win (an error result is not a successful session).
    reason = last_reason if result is not None else (end_reason or last_reason)
    achieved = (
        None
        if result is None
        else (not bool(result.get("is_error")) and last_reason == "completed")
    )
    if not session_id:
        session_id = engine_store.sha256_text(str(events_path))[:16]
    if not cwd:
        cwd = str(workspace)
    return SessionContext(
        session_id=session_id,
        host="claude-code",
        project_root=str((project_root or Path(cwd)).resolve()),
        workspace=str(Path(workspace).resolve()),
        events_path=str(Path(events_path).resolve()),
        started_at=started or _now(),
        ended_at=ended or _now(),
        end_reason=reason if reason in END_REASONS else "unknown",
        user_turns=turns,
        approvals={"allowed": 0, "rejected": 0},
        signals={
            "achieved": achieved,
            "delivered": None,
            "corrections": corrections,
            "tool_loops": loops,
            "failures": [] if reason == "completed" else ["failed-turn"],
        },
        trajectory_summary="\n".join(trajectory),
        usage=usage,
        cost_usd=result.get("total_cost_usd") if isinstance(result, dict) else None,
        source=f"claude-session:{Path(events_path).name}",
    )


def _codex_events(events: list[dict]) -> tuple[str, dict, int, str, str, list[dict]]:
    reason = "unknown"
    usage = {}
    tool_errors = 0
    trajectory = []
    turns: list[dict] = []
    session_id = ""
    last_failed = False
    for event in events:
        etype = str(event.get("type") or "")
        session_id = session_id or str(event.get("session_id") or event.get("thread_id") or "")
        if etype == "turn.completed":
            reason = "completed"
            raw = event.get("usage") or {}
            usage = {
                "input_tokens": raw.get("input_tokens", 0),
                "output_tokens": raw.get("output_tokens", 0),
                "cached_input_tokens": raw.get("cached_input_tokens", 0),
                "reasoning_tokens": raw.get("reasoning_output_tokens", 0),
            }
            trajectory.append("[HARNESS] turn.completed")
        elif etype in ("turn.failed", "error"):
            reason = "error"
            trajectory.append(f"[ERROR] {str(event.get('message') or event.get('error') or '')[:2000]}")
        elif etype == "item":
            item = event.get("item") or {}
            item_type = str(item.get("item_type") or item.get("type") or "")
            if item_type == "command_execution":
                command = str(item.get("command") or "")
                trajectory.append(f"[ACTION] command {command[:400]}")
                exit_code = item.get("exit_code")
                if exit_code not in (None, 0):
                    tool_errors += 1
                    trajectory.append(f"[OBSERVATION ERROR] exit {exit_code}")
                else:
                    trajectory.append("[OBSERVATION] command ok")
            elif item_type == "agent_message":
                text = str(item.get("text") or "")
                if text:
                    trajectory.append(f"[ASSISTANT] {_clip(text, 4000)}")
            elif item_type == "user_message":
                text = str(item.get("text") or "")
                if text:
                    turns.append(
                        {"text": text, "corrective": _is_corrective(text, last_failed) and bool(turns)}
                    )
                    trajectory.append(f"[USER] {_clip(text, 4000)}")
    return reason, usage, tool_errors, session_id, "\n".join(trajectory), turns


def ingest_codex(
    *,
    events_path: Path,
    workspace: Path,
    project_root: Path | None = None,
    end_reason: str | None = None,
    user_turns: list[dict] | None = None,
) -> SessionContext:
    events = _read_jsonl(Path(events_path))
    reason, usage, tool_errors, session_id, trajectory, parsed_turns = _codex_events(events)
    turns = user_turns or parsed_turns
    normalized = []
    for index, turn in enumerate(turns):
        text = str(turn.get("text", "")) if isinstance(turn, dict) else str(turn)
        if not text.strip():
            continue
        corrective = bool(turn.get("corrective")) if isinstance(turn, dict) else index > 0
        normalized.append({"text": text, "corrective": corrective})
    session_id = session_id or engine_store.sha256_text(str(events_path))[:16]
    cwd = str(workspace)
    reason = end_reason or reason
    return SessionContext(
        session_id=session_id,
        host="codex",
        project_root=str((project_root or Path(cwd)).resolve()),
        workspace=str(Path(workspace).resolve()),
        events_path=str(Path(events_path).resolve()),
        started_at=_now(),
        ended_at=_now(),
        end_reason=reason if reason in END_REASONS else "unknown",
        user_turns=normalized,
        approvals={"allowed": 0, "rejected": 0},
        signals={
            "achieved": None if reason == "unknown" else reason == "completed",
            "delivered": None,
            "corrections": sum(1 for turn in normalized[1:] if turn["corrective"]),
            "tool_loops": 1 if tool_errors >= 3 else 0,
            "failures": [] if reason == "completed" else ["failed-turn"],
        },
        trajectory_summary=trajectory,
        usage=usage,
        source=f"codex-session:{Path(events_path).name}",
    )


def ingest(
    *,
    host: str,
    events_path: Path,
    workspace: Path,
    project_root: Path | None = None,
    end_reason: str | None = None,
    user_turns: list[dict] | None = None,
) -> SessionContext:
    if not Path(events_path).is_file():
        raise ConsumerError("INVALID_REQUEST", "会话事件文件不存在。", "检查宿主会话路径。")
    if host == "dsh":
        return ingest_dsh(
            events_path=events_path,
            workspace=workspace,
            project_root=project_root,
            end_reason=end_reason,
        )
    if host == "claude-code":
        return ingest_claude(
            events_path=events_path,
            workspace=workspace,
            project_root=project_root,
            end_reason=end_reason,
        )
    if host == "codex":
        return ingest_codex(
            events_path=events_path,
            workspace=workspace,
            project_root=project_root,
            end_reason=end_reason,
            user_turns=user_turns,
        )
    raise ConsumerError("HOST_UNSUPPORTED", "不支持这个宿主。", "使用 dsh、claude-code 或 codex。")


def _redact_turns(turns: list[dict]) -> tuple[list[dict], list[str]]:
    labels: list[str] = []
    out = []
    for turn in turns:
        text, found = sanitize.redact_text(str(turn.get("text", "")))
        labels.extend(found)
        out.append({"text": text, "corrective": bool(turn.get("corrective"))})
    return out, labels


def save_session(
    store: ConsumerStore,
    context: SessionContext,
    *,
    source_workspace: Path | None = None,
    session_workspace: Path | None = None,
) -> dict:
    """Persist a SessionRecord and its private workspace copy.

    Incognito is checked *before* this function is called; no record is written for an
    incognito session.
    """
    projects = ProjectManager(store)
    project_root = Path(context.project_root or context.workspace)
    project = projects.for_root(project_root)
    turns, labels = _redact_turns(context.user_turns)
    summary, traj_labels = sanitize.redact_text(context.trajectory_summary)
    source = Path(source_workspace or context.workspace)
    target = session_workspace or (store.home / "sessions" / context.session_id / "workspace")
    redaction = {"removed_files": 0, "redacted_fields": sorted(set(labels + traj_labels))}
    record = {
        "version": 1,
        "id": context.session_id,
        "host": context.host,
        "project_id": project["id"],
        "project": str(project_root.resolve()),
        "workspace": str(target),
        "source_workspace": str(source),
        "events_path": context.events_path,
        "started_at": context.started_at or _now(),
        "ended_at": context.ended_at or _now(),
        "end_reason": context.end_reason,
        "signals": {
            **context.signals,
            "corrections": int(context.signals.get("corrections", 0) or 0),
            "tool_loops": int(context.signals.get("tool_loops", 0) or 0),
        },
        "user_turns": turns,
        "trajectory_summary": summary,
        "usage": context.usage,
        "cost_usd": context.cost_usd,
        "status": "recorded",
        "job_id": None,
        "redaction": redaction,
        "source": context.source,
        "created_at": _now(),
    }
    store.write_record("sessions", context.session_id, record)
    return record


def end_reason_from_exit(code: int | None, *, stderr: str = "") -> str:
    if code is None:
        return "interrupted"
    if code == 0:
        return "completed"
    if code in (130, 143):
        return "aborted"
    if code == 137:
        return "interrupted"
    if "max" in stderr.lower() and "token" in stderr.lower():
        return "max-tokens"
    return "error"


def iter_session_files(root: Path) -> Iterable[Path]:
    yield from engine_store.iter_files(root)
