"""Build runtime-independent tasks from dsh session logs."""

from __future__ import annotations

import io
import json
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from . import contract, sanitize, store

SESSION_FILE_RE = re.compile(r"^session(?:\.v(\d+))?\.jsonl(\.zstd)?$")
FAILURE_REASONS = {"error", "blocked", "max-tokens", "aborted"}
IGNORED_REASONS = {"interrupted", "forked", "completed"}


@dataclass(frozen=True)
class FailureRules:
    reasons: frozenset[str] = frozenset(FAILURE_REASONS)
    min_approval_rejects: int = 3
    min_tool_loops: int = 1
    include_corrections: bool = True


DEFAULT_FAILURE_RULES = FailureRules()
CORRECTION_CUES = (
    "no,",
    "nope",
    "wrong",
    "not what",
    "instead",
    "actually",
    "try again",
    "retry",
    "fix ",
    "still ",
    "没",
    "不对",
    "错了",
    "还是",
    "重试",
    "重新",
    "不要",
    "改为",
)


class SessionReadError(RuntimeError):
    pass


@dataclass
class SessionFinding:
    path: Path
    session_id: str
    header: dict
    events: list[dict]
    failures: list[str] = field(default_factory=list)
    turns: list[contract.UserTurn] = field(default_factory=list)
    last_reason: str = ""
    rejected_approvals: int = 0
    tool_error_loops: int = 0
    cwd: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.failures)

    def evidence(self) -> dict:
        return {
            "session_file": str(self.path),
            "session_id": self.session_id,
            "failures": self.failures,
            "last_turn_end": self.last_reason,
            "rejected_approvals": self.rejected_approvals,
            "tool_error_loops": self.tool_error_loops,
            "n_events": len(self.events),
            "n_user_turns": len(self.turns),
            "cwd": self.cwd,
        }


def _decompress(data: bytes) -> bytes:
    try:
        import zstandard
    except ImportError as exc:
        raise SessionReadError(
            "reading compressed dsh sessions requires the zstandard package"
        ) from exc
    try:
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)) as reader:
            return reader.read()
    except Exception as exc:
        raise SessionReadError(f"cannot decompress session log: {exc}") from exc


def _canonical_session_files(root: Path) -> list[Path]:
    candidates: dict[Path, int] = {}
    for path in Path(root).rglob("session*.jsonl*"):
        match = SESSION_FILE_RE.match(path.name)
        if not match or not path.is_file():
            continue
        version = int(match.group(1) or 0)
        session_dir = path.parent
        current = candidates.get(session_dir)
        if current is None or version > current:
            candidates[session_dir] = version
    out: list[Path] = []
    for session_dir, version in candidates.items():
        prefix = "session" if version == 0 else f"session.v{version}"
        for suffix in (".jsonl.zstd", ".jsonl"):
            path = session_dir / f"{prefix}{suffix}"
            if path.is_file():
                out.append(path)
                break
    return sorted(out)


def _parse_rows(path: Path) -> tuple[dict, list[dict]]:
    raw = path.read_bytes()
    if path.suffix == ".zstd":
        raw = _decompress(raw)
    text = raw.decode("utf-8", errors="replace").replace("\x00", "")
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    if not rows or rows[0].get("type") != "session":
        raise SessionReadError(f"{path}: first record is not a session header")
    header = rows[0]
    events = []
    for row in rows[1:]:
        if isinstance(row, dict) and isinstance(row.get("type"), str):
            events.append(row)
    return header, events


def _text_blocks(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(p for p in parts if p).strip()


def _user_turns(events: list[dict]) -> list[contract.UserTurn]:
    turns: list[contract.UserTurn] = []
    last_turn_failed = False
    for event in events:
        etype = event.get("type")
        if etype == "turn/end":
            reason = (event.get("data") or {}).get("reason") or {}
            kind = reason.get("kind") if isinstance(reason, dict) else None
            last_turn_failed = kind in FAILURE_REASONS
            continue
        if etype != "user/message":
            continue
        data = event.get("data") or {}
        message = data.get("message") if isinstance(data.get("message"), dict) else data
        source = message.get("source") or {}
        kind = source.get("kind") if isinstance(source, dict) else None
        if kind not in (None, "user"):
            continue
        text = _text_blocks(message.get("content"))
        if not text:
            continue
        if not turns:
            turns.append(contract.UserTurn(text=text, corrective=False))
            continue
        low = text.strip().lower()
        corrective = last_turn_failed or any(cue in low for cue in CORRECTION_CUES)
        turns.append(contract.UserTurn(text=text, corrective=corrective))
    return turns


def _tool_error_loops(events: list[dict]) -> int:
    calls: dict[str, str] = {}
    loops = 0
    streak = 0
    previous: tuple[str, str] | None = None
    for event in events:
        data = event.get("data") or {}
        if event.get("type") == "tool/call":
            call_id = str(data.get("callId") or "")
            if call_id:
                calls[call_id] = str(data.get("name") or "")
            continue
        if event.get("type") != "tool/result":
            continue
        message = data.get("message") or {}
        tool = calls.get(str(message.get("toolCallId") or message.get("callId") or ""), "")
        if not message.get("isError"):
            previous = None
            streak = 0
            continue
        error = data.get("error") or {}
        reason = str(error.get("code") or error.get("name") or "") or _text_blocks(
            message.get("content")
        )[:200]
        sig = (tool, reason)
        if sig == previous:
            streak += 1
        else:
            previous = sig
            streak = 1
        if streak >= 3:
            loops += 1
            streak = 0
    return loops


def read_session(path: Path, *, rules: FailureRules | None = None) -> SessionFinding:
    rules = rules or DEFAULT_FAILURE_RULES
    header, events = _parse_rows(Path(path))
    session_id = str(header.get("id") or Path(path).parent.name)
    turns = _user_turns(events)
    reasons: list[str] = []
    rejected = 0
    for event in events:
        data = event.get("data") or {}
        if event.get("type") == "turn/end":
            reason = data.get("reason") or {}
            kind = reason.get("kind") if isinstance(reason, dict) else None
            if isinstance(kind, str):
                reasons.append(kind)
        elif event.get("type") == "approval/decided":
            if data.get("outcome") == "rejected":
                rejected += 1
    loops = _tool_error_loops(events)
    failures: list[str] = []
    if any(r in rules.reasons for r in reasons):
        failures.append("failed-turn")
    if rejected >= int(rules.min_approval_rejects):
        failures.append("approval-rejected")
    if loops >= int(rules.min_tool_loops):
        failures.append("tool-error-loop")
    if rules.include_corrections and any(t.corrective for t in turns[1:]):
        failures.append("user-correction")
    if not turns:
        failures.append("no-user-instruction")
    return SessionFinding(
        path=Path(path),
        session_id=session_id,
        header=header,
        events=events,
        failures=failures,
        turns=turns,
        last_reason=reasons[-1] if reasons else "",
        rejected_approvals=rejected,
        tool_error_loops=loops,
        cwd=str(header.get("cwd") or ""),
    )


def discover_sessions(sessions_root: Path) -> list[Path]:
    return _canonical_session_files(Path(sessions_root))


@dataclass
class ImportResult:
    imported: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    split_path: Path | None = None

    def to_dict(self) -> dict:
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "split_path": str(self.split_path) if self.split_path else None,
        }


def _load_criteria(path: Path | None) -> tuple[list[dict], list[str], str | None]:
    if path is None or not path.is_file():
        return [], [], None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return payload, [], None
    if not isinstance(payload, dict):
        raise ValueError(f"criteria file {path} must be a check list or an object")
    checks = payload.get("checks") or []
    deliverables = payload.get("expected_deliverables") or []
    domain = payload.get("domain")
    if not isinstance(checks, list) or not isinstance(deliverables, list):
        raise ValueError(f"criteria file {path} has invalid checks/expected_deliverables")
    return checks, [str(d) for d in deliverables], str(domain) if domain else None


def _criteria_for(criteria_dir: Path | None, finding: SessionFinding) -> Path | None:
    if criteria_dir is None:
        return None
    for name in (finding.session_id, store.safe_slug(finding.session_id)):
        path = Path(criteria_dir) / f"{name}.json"
        if path.is_file():
            return path
    return None


def import_sessions(
    *,
    sessions_root: Path,
    task_store: contract.TaskStore,
    domain: str,
    workspace: Path | None = None,
    copy_workspace: bool = False,
    criteria_dir: Path | None = None,
    expected_deliverables: list[str] | None = None,
    max_sessions: int | None = None,
    include_all: bool = False,
    split: bool = True,
    holdout: float = 0.2,
    test: float = 0.5,
    seed: int = 0,
    rules: FailureRules | None = None,
) -> ImportResult:
    rules = rules or DEFAULT_FAILURE_RULES
    result = ImportResult()
    files = discover_sessions(sessions_root)
    prepared = 0
    task_store.root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="morphd-import-") as tmp:
        tmp_root = Path(tmp)
        for path in files:
            if max_sessions is not None and prepared >= max_sessions:
                break
            try:
                finding = read_session(path, rules=rules)
            except SessionReadError as exc:
                result.skipped.append({"session": str(path), "reason": str(exc)})
                continue
            if not finding.failed and not include_all:
                result.skipped.append(
                    {"session": str(path), "reason": "no failure signal"}
                )
                continue
            if not finding.turns:
                result.skipped.append({"session": str(path), "reason": "no user instruction"})
                continue
            try:
                criteria_checks, criteria_deliverables, criteria_domain = _load_criteria(
                    _criteria_for(criteria_dir, finding)
                )
            except (OSError, ValueError) as exc:
                result.skipped.append({"session": str(path), "reason": f"criteria: {exc}"})
                continue
            source_dir = workspace or (Path(finding.cwd) if copy_workspace and finding.cwd else None)
            if source_dir is not None and not source_dir.is_dir():
                result.skipped.append(
                    {"session": str(path), "reason": f"workspace not found: {source_dir}"}
                )
                continue
            if source_dir is None:
                source_dir = tmp_root / "empty-workspace"
                source_dir.mkdir(parents=True, exist_ok=True)
            sanitized = tmp_root / store.safe_slug(finding.session_id) / "workspace"
            report = sanitize.sanitize_tree(source_dir, sanitized)
            digest = store.tree_hash(sanitized)
            task_ref = "dsh-" + store.safe_slug(finding.session_id, max_len=90)
            if task_ref in task_store.all() or task_ref in result.imported:
                task_ref = f"{task_ref}-{store.sha256_text(finding.session_id)[:6]}"
            instruction = finding.turns[0].text
            contract_obj = contract.TaskContract.new(
                task_ref=task_ref,
                instruction=instruction,
                workspace_sha256=digest,
                domain=criteria_domain or domain,
                checks=criteria_checks,
                user_turns=finding.turns,
                expected_deliverables=expected_deliverables or criteria_deliverables,
                source_runtime="dsh",
                provenance={
                    "source": "dsh-session",
                    **finding.evidence(),
                    "sanitize": report.to_dict(),
                },
            )
            try:
                task_store.add_task(contract_obj, sanitized)
            except (ValueError, contract.ContractError) as exc:
                result.skipped.append({"session": str(path), "reason": str(exc)})
                continue
            result.imported.append(task_ref)
            prepared += 1
    if split and result.imported:
        if not task_store.split_path().exists():
            assignments = task_store.build_split_assignments(
                holdout=holdout, test=test, seed=seed
            )
            result.split_path = task_store.write_splits(assignments)
        else:
            result.split_path = task_store.split_path()
    return result


def session_summary(path: Path) -> dict:
    try:
        finding = read_session(path)
    except SessionReadError as exc:
        return {"session": str(path), "error": str(exc)}
    return finding.evidence()


def iter_session_summaries(root: Path) -> list[dict]:
    return [session_summary(path) for path in discover_sessions(root)]


__all__ = [
    "ImportResult",
    "SessionFinding",
    "SessionReadError",
    "discover_sessions",
    "import_sessions",
    "iter_session_summaries",
    "read_session",
]
