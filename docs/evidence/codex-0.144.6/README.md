# Codex 0.144.6 evidence

No codex CLI was installed in the build environment, so this directory records the **upstream
source contract** rather than a live call. Run the commands below on the execution machine and
store the output here (redact prompts/keys).

## Verified against upstream `harness2/substrate/codex.py` @ 1cfe254e8d25

- `CODEX_SURFACE`: `always_on=("AGENTS.md",)`, `always_on_max=8000`, `skill_dir=".agents/skills"`,
  `hooks_file=".codex/hooks.json"`.
- `CODEX_ROOT_ENTRIES=("AGENTS.md", ".agents", "scripts", ".codex")`.
- Reference argv in v0.5: `codex exec <task> --model <m> -c
  model_reasoning_effort="high" --skip-git-repo-check --sandbox workspace-write
  --ask-for-approval never --ephemeral --json -C <ws> --add-dir <ws>`.
- The old `--dangerously-bypass-*` argv is intentionally retired. If a pinned Codex cannot
  provide a usable non-bypass sandbox, Codex is beta/unsupported rather than weakened.
- Environment: `CODEX_HOME` plus a PATH prefix, inherited `OPENAI_BASE_URL` removed.
- Trajectory: item stream deduped by `item.id` (last state wins, first position kept).
- Usage: `turn.completed.usage` with `input_tokens`, `cached_input_tokens`, `output_tokens`,
  `reasoning_output_tokens`.

## Required live checks (M0)

```sh
codex --version
codex exec --help | tee codex-exec-help.txt
codex exec resume --help | tee codex-resume-help.txt
```

Store the help output next to this file. The adapter requires `--sandbox` and `--ask-for-approval` in `exec --help` and detects
resume support from these help commands at preflight; if it is absent, convergence is reported as `n/a` and the run is
single-turn.

The upstream argv **bypasses approvals and sandboxing**. The container and egress policy are
the security boundary; see `deploy/container/README.md`.
