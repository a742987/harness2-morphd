# Claude Code 2.1.302 evidence

Captured from a real local `claude --version` / `claude --help` on 2026-10-21 (Windows host,
WSL call). No model call was made for this snapshot.

- `version.txt`: `2.1.302 (Claude Code)`
- `help.txt`: full help output as captured.

## Four M0 contract items still pending

FR-12 requires a sanitised, real-call snapshot of each item under this directory **before**
Claude metrics may be judged (otherwise `n/a`).

1. **`CLAUDE.md` loading** — subdirectory scope, budget, truncation, `@import` semantics.
2. **Project discovery** — whether `.claude/skills/<name>/SKILL.md` and
   `.claude/agents/<name>.md` are auto-discovered by 2.1.302 and their required frontmatter.
3. **`stream-json` schema** — exact `system` / `assistant` / `user` / `result` shapes,
   `usage` field names, `total_cost_usd`, `num_turns`.
4. **Completion and resume** — `result.subtype` / `is_error` and exit-code mapping, and whether
   `--session-id`/`--resume` require an existing session.

Use `scripts/capture_claude_contract.sh <workspace> <output-dir>` inside the isolated container.
It sets `--max-budget-usd` as a backstop and writes raw stream files for review. Redact the
prompt and any workspace content before committing.

The adapter (`morphd/runtimes/claudecode.py`) implements the documented schema and **fails
closed** on an unknown top-level event type. It does not fabricate metrics when the result
event is missing.

The following flags are required by preflight and were present in the captured help:

`-p/--print`, `--output-format`, `--permission-mode`, `--allowedTools`, `--disallowedTools`,
`--setting-sources`, `--session-id`, `--resume`, `--model`, `--effort`, `--max-budget-usd`,
`--add-dir`, `--agents`, `--settings`, `--restricted`, `--no-session-persistence`.
