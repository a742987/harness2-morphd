"""Codex CLI adapter, reusing the upstream CodexSubstrate unchanged."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .. import _upstream, budget, config, pricing, sanitize, store
from ..contract import TaskContract, TaskStore
from ..runtimes.base import BaseAdapter, PreparedRun, TurnResult
from .base import RuntimeError_

CX = _upstream.substrate("codex")
CODEX_VERSION = "0.144.6"


def _find_key(payload, names: tuple[str, ...]) -> str | None:
    if isinstance(payload, dict):
        for name in names:
            value = payload.get(name)
            if isinstance(value, str) and value.strip():
                return value
        for value in payload.values():
            found = _find_key(value, names)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_key(value, names)
            if found:
                return found
    return None


def _usage(events: list[dict]) -> dict:
    totals = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    present = False
    for event in events:
        if event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        present = True
        for key in totals:
            try:
                totals[key] += int(usage.get(key, 0) or 0)
            except (TypeError, ValueError):
                pass
    totals["present"] = present
    return totals


class CodexAdapter(BaseAdapter):
    runtime = "codex"
    substrate_name = "cx"

    def __init__(self, *, proposer_channel: str = "claude_cli", proposer_model: str = "", **kwargs):
        super().__init__(**kwargs)
        self.substrate = CX.CodexSubstrate()
        from ..runtimes.prompts import make_spec

        self.spec = make_spec(
            runtime=self.runtime,
            substrate=self.substrate_name,
            settings=self.settings,
            install_mode="codex_root",
            allow_subagents=True,
            proposer_channel=proposer_channel,
            proposer_model=proposer_model,
        )
        self._resume_style: str | None = None

    def _hard_always_on_bytes(self) -> int:
        return 32_768

    def _runtime_controls(self) -> dict:
        return {
            "sandbox": "workspace-write",
            "approval": "never",
            "hook_trust": "disabled",
            "reasoning_effort": self.settings.codex_reasoning_effort,
            "replay": self._resume_style or "single-turn",
            "root_entries": list(CX.CODEX_ROOT_ENTRIES),
        }

    def _binary(self) -> str:
        binary = self.settings.binary_for("codex")
        resolved = shutil.which(binary) if os.sep not in binary else binary
        if not resolved or not Path(resolved).exists():
            raise RuntimeError_(
                f"codex CLI not found ({binary!r}); install codex "
                f"{self.settings.pins['codex']} and set MORPHD_CODEX_BIN if needed"
            )
        return str(resolved)

    def _runtime_version(self) -> str:
        if self._version_cache is not None:
            return self._version_cache
        proc = subprocess.run(
            [self._binary(), "--version"], capture_output=True, text=True, timeout=20, check=False
        )
        out = (proc.stdout or proc.stderr or "").strip()
        if proc.returncode != 0 or not out:
            raise RuntimeError_(f"cannot read codex version: exit {proc.returncode}")
        self._version_cache = out.splitlines()[0].strip()
        return self._version_cache

    def _detect_resume(self) -> str:
        if self._resume_style is not None:
            return self._resume_style
        binary = self._binary()
        for args, style in (
            (["exec", "resume", "--help"], "exec-resume"),
            (["resume", "--help"], "resume"),
        ):
            proc = subprocess.run(
                [binary, *args], capture_output=True, text=True, timeout=20, check=False
            )
            if proc.returncode == 0:
                self._resume_style = style
                return style
        self._resume_style = ""
        return ""

    def _preflight_runtime(self) -> dict:
        help_proc = subprocess.run(
            [self._binary(), "exec", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        help_text = (help_proc.stdout or "") + (help_proc.stderr or "")
        required = ["--json", "--sandbox", "--ask-for-approval"]
        missing = [flag for flag in required if flag not in help_text]
        if help_proc.returncode != 0 or missing:
            raise RuntimeError_(
                "codex exec does not accept the pinned reference flags "
                f"(missing {missing}); re-run compatibility checks before upgrading"
            )
        style = self._detect_resume()
        return {"exec_help_ok": True, "resume_style": style or "single-turn"}

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
        for rel in (".codex/hooks.json", ".codex/config.toml"):
            path = workspace / rel
            if path.exists():
                path.unlink()
        shutil.rmtree(workspace / ".agents" / "skills", ignore_errors=True)
        install = self.substrate.materialise(
            source_tree, self.spec, {"dest": run_dir, "run_id": run_id}
        )
        staged = Path(install.staging_dir)
        for entry in CX.CODEX_ROOT_ENTRIES:
            src = staged / entry
            if not src.exists():
                continue
            dst = workspace / entry
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
        shutil.rmtree(staged, ignore_errors=True)
        codex_home = run_dir / "codex_home"
        codex_home.mkdir(exist_ok=True)
        template = os.environ.get("MORPHD_CODEX_HOME_TEMPLATE", "").strip()
        if template:
            template_path = Path(template).expanduser().resolve()
            if not template_path.is_dir():
                raise RuntimeError_(f"MORPHD_CODEX_HOME_TEMPLATE is not a directory: {template_path}")
            shutil.copytree(template_path, codex_home, dirs_exist_ok=True)
        for forbidden in ("auth.json", "credentials.json", ".credentials.yaml"):
            path = codex_home / forbidden
            if path.exists():
                path.unlink()
        findings = sanitize.scan_paths([codex_home], max_file_size=1_000_000)
        if findings:
            raise RuntimeError_(
                "CODEX_HOME template contains credential-shaped content; templates must "
                "reference secrets by environment, never store them"
            )
        (run_dir / "home").mkdir(exist_ok=True)
        return PreparedRun(
            runtime_home=codex_home,
            workspace=workspace,
            extras={"fingerprint": install.fingerprint},
        )

    def _base_argv(self, prompt: str, workspace: Path, *, ephemeral: bool) -> list[str]:
        prompt = prompt if not prompt.startswith("-") else " " + prompt
        argv = [
            self._binary(),
            "exec",
            prompt,
            "--model",
            self.model,
            "-c",
            f'model_reasoning_effort="{self.settings.codex_reasoning_effort}"',
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
            "--json",
            "-C",
            str(workspace),
            "--add-dir",
            str(workspace),
        ]
        if ephemeral:
            argv.insert(-4, "--ephemeral")
        return argv

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
        stream_path = events_dir / f"codex-turn-{turn_index + 1}-stream.jsonl"
        stderr_path = events_dir / f"codex-turn-{turn_index + 1}-stderr.log"
        style = self._detect_resume()
        continuing = bool(session_id and style)
        ephemeral = not style
        prompt_arg = prompt if not prompt.startswith("-") else " " + prompt
        if continuing and style == "exec-resume":
            argv = [
                self._binary(),
                "exec",
                "resume",
                str(session_id),
                prompt_arg,
                "--model",
                self.model,
                "-c",
                f'model_reasoning_effort="{self.settings.codex_reasoning_effort}"',
                "--skip-git-repo-check",
                "--sandbox",
                "workspace-write",
                "--ask-for-approval",
                "never",
                "--json",
                "-C",
                str(workspace),
                "--add-dir",
                str(workspace),
            ]
        elif continuing:
            argv = [
                self._binary(),
                "resume",
                str(session_id),
                prompt_arg,
                "--json",
            ]
        else:
            argv = self._base_argv(prompt, workspace, ephemeral=ephemeral)
        env = dict(os.environ)
        env.pop("OPENAI_BASE_URL", None)
        env["CODEX_HOME"] = str(runtime_home)
        env["HOME"] = str(run_dir / "home")
        env["NO_COLOR"] = "1"
        timed_out = False
        try:
            with open(stream_path, "wb") as out, open(stderr_path, "wb") as err:
                proc = subprocess.run(
                    argv,
                    cwd=workspace,
                    env=env,
                    stdout=out,
                    stderr=err,
                    timeout=timeout,
                    check=False,
                )
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = 124
        events = CX.parse_items(stream_path)
        sid = session_id
        if not sid:
            for event in events:
                sid = _find_key(event, ("thread_id", "session_id", "conversation_id", "threadId"))
                if sid:
                    break
        usage = _usage(events)
        completed = any(event.get("type") == "turn.completed" for event in events)
        failed = any(
            event.get("type") in ("turn.failed", "error")
            or isinstance(event.get("error"), (dict, str))
            for event in events
        )
        if timed_out:
            achieved = False
            reason = "timeout"
        elif failed:
            achieved = False
            reason = "error"
        elif completed and exit_code == 0:
            achieved = True
            reason = "completed"
        elif completed:
            achieved = False
            reason = f"exit-{exit_code}"
        elif exit_code == 0:
            achieved = None
            reason = "no-completion-event"
        else:
            achieved = False
            reason = f"exit-{exit_code}"
        tool_events = []
        for event in events:
            item = event.get("item") if isinstance(event, dict) else None
            if not isinstance(item, dict) or item.get("type") != "command_execution":
                continue
            exit_value = item.get("exit_code")
            error = item.get("status") == "failed" or exit_value not in (None, 0)
            signature = str(exit_value) if error else ""
            tool_events.append(
                {
                    "tool": "command_execution",
                    "error": bool(error),
                    "signature": signature
                    or str(item.get("aggregated_output") or "")[:200],
                }
            )
        turns = sum(1 for event in events if event.get("type") == "turn.completed")
        final_text = CX.final_message(stream_path)
        combined = events_dir / "codex.jsonl"
        with open(combined, "ab") as fh:
            if stream_path.is_file():
                fh.write(stream_path.read_bytes())
                if not stream_path.read_bytes().endswith(b"\n"):
                    fh.write(b"\n")
        return TurnResult(
            exit_code=exit_code,
            achieved=achieved,
            reason=reason,
            bucket=(
                "completed"
                if achieved is True
                else ("error" if achieved is False else "unavailable")
            ),
            session_id=sid,
            usage=usage,
            tool_events=tool_events,
            turns=turns or (1 if events else 0),
            final_text=final_text,
            stderr=store.read_text_limited(stderr_path, 4000),
            can_continue=bool(sid and style),
        )

    def _can_continue(self, result: TurnResult, session_id: str | None) -> bool:
        return bool(self._detect_resume() and session_id and result.can_continue)

    def _trajectory_path(self, run_dir: Path) -> Path:
        return Path(run_dir) / "events" / "codex.jsonl"


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
) -> CodexAdapter:
    return CodexAdapter(
        tasks=tasks,
        settings=settings,
        price=price,
        guard=guard,
        model=model,
        runs_root=runs_root,
        proposer_channel=proposer_channel,
        proposer_model=proposer_model,
    )
