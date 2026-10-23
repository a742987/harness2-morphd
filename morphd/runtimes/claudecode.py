"""Claude Code substrate and adapter (no upstream substrate exists)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

from .. import _upstream, budget, config, pricing, store, winproc
from ..contract import TaskContract, TaskStore
from ..runtimes.base import BaseAdapter, PreparedRun, TurnResult
from .base import RuntimeError_

HT = _upstream.core("tree")

CLAUDE_SURFACE = HT.SurfaceSpec(
    name="claude-code",
    always_on=("CLAUDE.md",),
    always_on_max=32_768,
    always_on_cap_reason=(
        "CLAUDE.md is loaded whole into every turn; an unverified larger budget would risk "
        "silent truncation at 32,768 bytes"
    ),
    skill_dir=".claude/skills",
    skill_name_rule="must_match",
    trigger_hint=(
        "Fix it by naming the skill in CLAUDE.md, e.g. 'When <the situation this skill "
        "handles>, use the {name} skill.'"
    ),
    autotrigger_file="CLAUDE.md",
    trigger_line="- When {desc}, use the {name} skill.\n",
    subagent_requires_mode=False,
    plugin_dir=None,
    hooks_file=None,
    compile_skill_scripts=True,
    reject_empty_always_on=True,
    stubs=(
        ("scripts/README.md", "<!-- c7: deterministic helpers, run as `python3 scripts/<name>.py`. -->\n"),
        (
            ".claude/skills/README.md",
            "<!-- c6: project skills, discovered from .claude/skills/<name>/SKILL.md. -->\n",
        ),
        (
            ".claude/agents/README.md",
            "<!-- c5: subagents are written from sub_agents/<name>/AGENT.md. -->\n",
        ),
    ),
)

SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
KNOWN_EVENT_TYPES = {
    "system",
    "assistant",
    "user",
    "result",
    "error",
    "stream_event",
    "rate_limit_event",
    "tool_progress",
    "auth_status",
    "control_request",
    "control_response",
}


def _content_blocks(message: dict) -> list[dict]:
    content = message.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _text_of_blocks(blocks: list[dict]) -> str:
    return "\n".join(
        block.get("text", "")
        for block in blocks
        if block.get("type") == "text" and isinstance(block.get("text"), str)
    ).strip()


def parse_events(path: Path) -> list[dict]:
    out: list[dict] = []
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("type"), str):
            out.append(event)
    return out


def strict_parse(path: Path) -> list[dict]:
    events = parse_events(path)
    unknown = sorted({str(e.get("type")) for e in events} - KNOWN_EVENT_TYPES)
    if unknown:
        raise RuntimeError_(
            f"unknown Claude Code stream-json event type(s) {unknown}; the parser fails "
            "closed instead of returning an empty trajectory. Capture a new contract "
            "snapshot before upgrading."
        )
    return events


def render_events(events: list[dict], *, obs_trunc: int, args_trunc: int = 400) -> str:
    lines: list[str] = []
    for event in events:
        etype = event.get("type")
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        if etype == "assistant":
            for block in _content_blocks(message):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    lines.append(f"[ASSISTANT] {str(block['text'])[:obs_trunc]}")
                elif block.get("type") == "tool_use":
                    args = json.dumps(block.get("input", {}), ensure_ascii=False)
                    lines.append(
                        f"[ACTION] {block.get('name', 'tool')}: {args[:args_trunc]}"
                    )
        elif etype == "user":
            for block in _content_blocks(message):
                if block.get("type") == "tool_result":
                    body = block.get("content")
                    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                    tag = "OBSERVATION ERROR" if block.get("is_error") else "OBSERVATION"
                    lines.append(f"[{tag}] {str(text)[:obs_trunc]}")
                elif block.get("type") == "text" and isinstance(block.get("text"), str):
                    lines.append(f"[USER] {str(block['text'])[:obs_trunc]}")
        elif etype == "system":
            subtype = event.get("subtype")
            if subtype:
                lines.append(f"[HARNESS] system: {subtype}")
        elif etype == "result":
            subtype = event.get("subtype")
            text = event.get("result")
            lines.append(f"[HARNESS] result: {subtype}")
            if isinstance(text, str) and text.strip():
                lines.append(f"[ASSISTANT] {text[:obs_trunc]}")
        elif etype == "error":
            lines.append(f"[ERROR] {str(event.get('error') or event)[:1000]}")
    rendered = "\n".join(lines).strip()
    return rendered or "(no parseable events in Claude stream)"


class ClaudeCodeSubstrate:
    name = "claude-code"

    def surface(self):
        return CLAUDE_SURFACE

    def materialise(self, tree_dir: Path, spec, ctx) -> object:
        base = _upstream.substrate("base")
        tree = HT.read_tree(CLAUDE_SURFACE, tree_dir)
        dest = Path(ctx.get("dest") or ".")
        home = dest / "runtime_home"
        overlay = dest / "workspace_overlay"
        overlay.mkdir(parents=True, exist_ok=True)
        (home / "home").mkdir(parents=True, exist_ok=True)
        (home / "config").mkdir(parents=True, exist_ok=True)
        for rel, text in tree.items():
            if rel == "CLAUDE.md" or rel.startswith("scripts/"):
                store.atomic_write_text(overlay / rel, text)
            elif rel.startswith(".claude/skills/"):
                store.atomic_write_text(overlay / rel, text)
            elif rel.startswith("sub_agents/") and rel.endswith("/AGENT.md"):
                name = rel.split("/")[1]
                target = overlay / ".claude" / "agents" / f"{name}.md"
                body = text
                if not body.lstrip().startswith("---"):
                    body = f"---\nname: {name}\n---\n\n{body}"
                store.atomic_write_text(target, body)
        settings = {
            "permissions": {"deny": ["WebFetch", "WebSearch"]},
        }
        store.atomic_write_json(overlay / ".claude" / "settings.json", settings)
        fingerprint = store.payload_hash(tree)
        return base.Install(
            staging_dir=home,
            fingerprint=fingerprint,
            extras={
                "workspace_overlay": str(overlay),
                "home": str(home / "home"),
                "config": str(home / "config"),
            },
        )

    def execute(self, task_ref, install, run_ids, timeout):
        raise RuntimeError_("ClaudeCodeSubstrate.execute requires the adapter's launcher")

    def read_trajectory(self, stream_path: Path, *, obs_trunc: int, max_chars: int, args_trunc: int = 400) -> str:
        rendered = render_events(
            parse_events(Path(stream_path)), obs_trunc=obs_trunc, args_trunc=args_trunc
        )
        if len(rendered) > max_chars:
            rendered = rendered[:max_chars] + f"…(+{len(rendered) - max_chars} chars)"
        return rendered

    def preflight(self) -> None:
        pass


class ClaudeCodeAdapter(BaseAdapter):
    runtime = "claude-code"
    substrate_name = "claude-code"

    def __init__(self, *, proposer_channel: str = "claude_cli", proposer_model: str = "", **kwargs):
        super().__init__(**kwargs)
        self.substrate = ClaudeCodeSubstrate()
        from ..runtimes.prompts import make_spec

        self.spec = make_spec(
            runtime=self.runtime,
            substrate=self.substrate_name,
            settings=self.settings,
            install_mode="workspace_agents_md",
            allow_subagents=True,
            proposer_channel=proposer_channel,
            proposer_model=proposer_model,
        )

    def _hard_always_on_bytes(self) -> int:
        return 32_768

    def validate_tree(self, tree: dict[str, str]) -> None:
        super().validate_tree(tree)
        problems = []
        for rel in tree:
            if rel.startswith(".claude/hooks") or rel == ".claude/settings.json":
                problems.append(f"{rel}: c8 hooks/settings may not be edited")
            match = re.fullmatch(r"\.claude/skills/([^/]+)/SKILL\.md", rel)
            if match and not SKILL_NAME_RE.fullmatch(match.group(1)):
                problems.append(f"{rel}: skill names must be kebab-case")
        if problems:
            raise ValueError(" | ".join(problems))

    def _runtime_controls(self) -> dict:
        return {
            "permission_mode": self.settings.claude_permission_mode,
            "allowed_tools": sorted(
                s.strip() for s in self.settings.claude_allowed_tools.split(",") if s.strip()
            ),
            "disallowed_tools": sorted(
                s.strip() for s in self.settings.claude_disallowed_tools.split(",") if s.strip()
            ),
            "setting_sources": "project",
            "effort": self.settings.claude_effort,
            "no_user_settings": True,
        }

    def _binary(self) -> str:
        binary = self.settings.binary_for("claude-code")
        resolved = shutil.which(binary) if os.sep not in binary else binary
        if not resolved or not Path(resolved).exists():
            raise RuntimeError_(
                f"claude CLI not found ({binary!r}); install claude "
                f"{self.settings.pins['claude-code']} and set MORPHD_CLAUDE_BIN if needed"
            )
        return str(resolved)

    def _runtime_version(self) -> str:
        if self._version_cache is not None:
            return self._version_cache
        proc = subprocess.run(
            winproc.command_line(self._binary(), ["--version"]), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, check=False
        )
        out = (proc.stdout or proc.stderr or "").strip()
        if proc.returncode != 0 or not out:
            raise RuntimeError_(f"cannot read claude version: exit {proc.returncode}")
        self._version_cache = out.splitlines()[0].strip()
        return self._version_cache

    def _preflight_runtime(self) -> dict:
        proc = subprocess.run(
            winproc.command_line(self._binary(), ["--help"]),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        help_text = (proc.stdout or "") + (proc.stderr or "")
        required = [
            "--permission-mode",
            "--allowedTools",
            "--setting-sources",
            "--session-id",
            "--resume",
            "--output-format",
            "--max-budget-usd",
            "--strict-mcp-config",
        ]
        missing = [flag for flag in required if flag not in help_text]
        if proc.returncode != 0 or missing:
            raise RuntimeError_(
                "claude CLI does not expose the required flags "
                f"(missing {missing}); do not upgrade without a new contract snapshot"
            )
        return {"help_ok": True, "missing_flags": []}

    def _prepare_run(
        self,
        *,
        task: TaskContract,
        source_tree: Path,
        run_dir: Path,
        workspace: Path,
        run_id: str,
        meta: dict,
    ) -> PreparedRun:
        for rel in (".claude/settings.json", ".claude/settings.local.json", ".mcp.json"):
            path = workspace / rel
            if path.exists():
                path.unlink()
        shutil.rmtree(workspace / ".claude" / "hooks", ignore_errors=True)
        shutil.rmtree(workspace / ".claude" / "skills", ignore_errors=True)
        shutil.rmtree(workspace / ".claude" / "agents", ignore_errors=True)
        install = self.substrate.materialise(
            source_tree, self.spec, {"dest": run_dir, "run_id": run_id}
        )
        overlay = Path(install.extras["workspace_overlay"])
        shutil.copytree(overlay, workspace, dirs_exist_ok=True)
        return PreparedRun(
            runtime_home=Path(install.extras["home"]),
            workspace=workspace,
            extras={
                "home": install.extras["home"],
                "config": install.extras["config"],
                "fingerprint": install.fingerprint,
            },
        )

    def _invoke_turn(
        self,
        *,
        task: TaskContract,
        run_dir: Path,
        workspace: Path,
        runtime_home: Path,
        prompt: str,
        session_id: str | None,
        turn_index: int,
        timeout: int,
    ) -> TurnResult:
        events_dir = run_dir / "events"
        events_dir.mkdir(exist_ok=True)
        stream_path = events_dir / f"claude-turn-{turn_index + 1}-stream.jsonl"
        stderr_path = events_dir / f"claude-turn-{turn_index + 1}-stderr.log"
        new_session = session_id is None
        sid = str(uuid.uuid4()) if new_session else str(session_id)
        argv = [
            self._binary(),
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            self.settings.claude_permission_mode,
            "--allowedTools",
            self.settings.claude_allowed_tools,
            "--disallowedTools",
            self.settings.claude_disallowed_tools,
            "--setting-sources",
            "project",
            "--model",
            self.model,
            "--effort",
            self.settings.claude_effort,
            "--add-dir",
            str(workspace),
            "--strict-mcp-config",
        ]
        if new_session:
            argv += ["--session-id", sid]
        else:
            argv += ["--resume", sid]
        max_budget = os.environ.get("MORPHD_CLAUDE_MAX_BUDGET_USD", "").strip()
        if max_budget:
            argv += ["--max-budget-usd", max_budget]
        env = dict(os.environ)
        env["HOME"] = str(runtime_home)
        env["CLAUDE_CONFIG_DIR"] = str(run_dir / "claude_config")
        env["XDG_CONFIG_HOME"] = str(run_dir / "claude_config")
        env["NO_COLOR"] = "1"
        runtime_home.mkdir(parents=True, exist_ok=True)
        (run_dir / "claude_config").mkdir(exist_ok=True)
        timed_out = False
        argv = winproc.command_line(argv[0], argv[1:])
        try:
            with open(stream_path, "w", encoding="utf-8") as out, open(
                stderr_path, "w", encoding="utf-8"
            ) as err:
                proc = subprocess.run(
                    argv,
                    cwd=workspace,
                    env=env,
                    input=prompt,
                    text=True,
                    stdout=out,
                    stderr=err,
                    timeout=timeout,
                    check=False,
                )
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = 124
        events = strict_parse(stream_path)
        result = None
        for event in events:
            if event.get("type") == "result":
                result = event
        if result is None and not timed_out:
            raise RuntimeError_(
                f"Claude Code produced no `result` event (exit {exit_code}); the stream "
                "contract is unknown, so the run is not scored"
            )
        combined = events_dir / "claude.jsonl"
        with open(combined, "ab") as fh:
            if stream_path.is_file():
                data = stream_path.read_bytes()
                fh.write(data)
                if data and not data.endswith(b"\n"):
                    fh.write(b"\n")
        result = result or {}
        subtype = str(result.get("subtype") or "")
        is_error = bool(result.get("is_error"))
        if timed_out:
            achieved = False
            reason = "timeout"
        elif subtype == "success" and not is_error and exit_code == 0:
            achieved = True
            reason = "completed"
        elif subtype.startswith("error_max_turns"):
            achieved = False
            reason = "max-tokens"
        elif subtype.startswith("error_max_budget"):
            achieved = False
            reason = "aborted"
        elif is_error or subtype.startswith("error_"):
            achieved = False
            reason = subtype or "error"
        elif subtype:
            achieved = None
            reason = f"unknown-subtype:{subtype}"
        else:
            achieved = None
            reason = f"exit-{exit_code}" if exit_code else "unknown-result"
        usage = dict(result.get("usage") or {}) if isinstance(result.get("usage"), dict) else {}
        if result.get("total_cost_usd") is not None:
            usage["total_cost_usd"] = result.get("total_cost_usd")
        usage["present"] = bool(usage)
        tool_calls: dict[str, str] = {}
        tool_events: list[dict] = []
        assistant_text = ""
        for event in events:
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            if event.get("type") == "assistant":
                for block in _content_blocks(message):
                    if block.get("type") == "tool_use":
                        tool_calls[str(block.get("id") or "")] = str(block.get("name") or "tool")
                    elif block.get("type") == "text" and isinstance(block.get("text"), str):
                        assistant_text = block["text"]
            elif event.get("type") == "user":
                for block in _content_blocks(message):
                    if block.get("type") != "tool_result":
                        continue
                    call_id = str(block.get("tool_use_id") or "")
                    error = bool(block.get("is_error"))
                    body = block.get("content")
                    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
                    tool_events.append(
                        {
                            "tool": tool_calls.get(call_id, "tool"),
                            "error": error,
                            "signature": (text or "")[:200],
                        }
                    )
        result_session = str(result.get("session_id") or "")
        if not result_session:
            for event in events:
                value = event.get("session_id")
                if isinstance(value, str) and value:
                    result_session = value
                    break
        num_turns = result.get("num_turns")
        try:
            turns = int(num_turns)
        except (TypeError, ValueError):
            turns = sum(1 for e in events if e.get("type") == "assistant") or 0
        final_text = result.get("result")
        if not isinstance(final_text, str) or not final_text.strip():
            final_text = assistant_text
        bucket = "completed" if achieved is True else "unavailable"
        if achieved is False:
            bucket = "max-tokens" if reason == "max-tokens" else "aborted" if reason == "aborted" else "error"
        return TurnResult(
            exit_code=exit_code,
            achieved=achieved,
            reason=reason,
            bucket=bucket,
            session_id=result_session or sid,
            usage=usage,
            tool_events=tool_events,
            turns=turns or (1 if events else 0),
            final_text=final_text,
            stderr=store.read_text_limited(stderr_path, 4000),
            can_continue=bool(result_session or sid) and not timed_out,
        )

    def _trajectory_path(self, run_dir: Path) -> Path:
        return Path(run_dir) / "events" / "claude.jsonl"


def build_adapter(
    *,
    tasks: TaskStore,
    settings: config.Settings,
    price: pricing.Pricing,
    guard: budget.BudgetGuard,
    model: str,
    runs_root: Path | None = None,
    proposer_channel: str = "claude_cli",
    proposer_model: str = "",
) -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter(
        tasks=tasks,
        settings=settings,
        price=price,
        guard=guard,
        model=model,
        runs_root=runs_root,
        proposer_channel=proposer_channel,
        proposer_model=proposer_model,
    )
