# Architecture

harness2-morphd v0.5 has two layers:

1. **Engine** (`morphd/`): the pinned Harness² recursion, runtime substrates/adapters, task
   contract, run evidence, metrics, budgets, security scanning, evaluation and promotion.
2. **Consumer layer** (`morphd/consumer/`): settings, BYOK/Credential Manager, host discovery and
   adaptation, session ingestion, in-host job runner, candidate/trial management, local API,
   Web UI assets, tray helper and the process lifecycle.

The Web UI is a client. It contains no enforcement logic: all safety, budget, state-machine and
policy decisions happen in the local engine/API.

```
Host process / wrapper
  └─ harness2-morphd local service
       ├─ lifecycle manager: startup, shutdown, checkpoint, recovery
       ├─ session observer: dsh log watcher / Claude SessionEnd hook / Codex wrapper exit
       ├─ job runner (concurrency 1): triage → propose → validate → candidate
       ├─ BYOK provider client + proposer (tools or JSON patch)
       ├─ candidate manager: snapshot, apply, trial, rollback, restore original
       ├─ budget ledger: monthly / session / job hard limits
       ├─ attention manager: pause, vacation, incognito, focus, quiet hours, caps, feedback
       ├─ privacy: redaction, send preview, export, retention, deletion
       ├─ loopback HTTP API + static Web UI
       └─ Windows Credential Manager / encrypted vault

Browser or Electron BrowserWindow ── same local UI ──> 127.0.0.1:<random port>
Tray helper / Electron tray ── host lifecycle ──> disappears with the host
```

## Process model

- The UI server, job runner, watchers and tray helper exist **only while the host server is
  alive**. `HostRuntime.stop()` closes the HTTP port, stops the worker, writes checkpoints and
  removes `runtime/server.json`.
- `morphd host run --host <kind> --binary <real> -- <args>` starts the service, prints the UI URL
  to stderr, launches the real host, and waits. On host exit it ingests the latest session,
  pauses jobs and stops the service. It never detaches or retries in the background.
- For dsh, when no native close event is available, a conservative watcher treats a session as
  ended only if the last event is `turn/end` and the log has been unchanged for
  `MORPHD_SESSION_IDLE_S` (default 60s). This is the PRD's "last turn/end + idle" fallback, not
  a session/end assumption.
- Claude Code installs a `SessionEnd` hook in `settings.local.json` (backed up/restored). The
  hook posts to the running local server or spools the event for the next host start.
- Codex uses the wrapper process exit as session end; analysis resumes from the persisted queue
  on the next host start.
- There is no Windows Task Scheduler entry, daemon, cron, cloud queue, service-worker sync,
  or auto-start.

## Host discovery and adaptation

Discovery only checks:

1. `PATH` executables;
2. npm global locations;
3. common install directories;
4. common config directories and explicit env vars (`DSH_HOME`, `CLAUDE_CONFIG_DIR`,
   `CODEX_HOME`);
5. existing install records.

It validates identity through `--version` output and config structure, not a directory name.
Unsupported versions are listed but never written. Adaptation:

1. validate executable/version/config writability;
2. back up every file it will modify;
3. write a wrapper (all hosts) and the Claude `SessionEnd` hook when available;
4. run a zero-model probe;
5. record an `InstallRecord`; roll back on any failure.

`uninstall` reverses the record: restores backups, deletes created wrapper/hook files, removes
the install record. It never deletes host files or user data. The first candidate apply creates
the first snapshot, pinned for "restore original harness".

## Data layout

```
%USERPROFILE%\.morphd\
  config.json          # non-secret settings
  providers.json       # provider profiles; key_ref only
  state.json           # pause/vacation/incognito/focus/weekly/feedback
  vault.enc            # encrypted fallback vault (AES-GCM, scrypt)
  projects/<id>.json
  sessions/<id>.json   # redacted session record
  sessions/<id>/workspace/
  jobs/<id>.json + jobs/<id>/...
  candidates/<id>.json
  diffs/<id>.diff
  snapshots/<id>.json + snapshots/<id>/files/...
  logs/events.jsonl    # redacted, 100MB roll
  exports/
  runtime/server.json  # user-private, random port + host token + UI URL
  install.json
  _budget/consumer.jsonl
```

Atomic JSON writes; the data directory inherits the Windows user-profile ACL. Unknown fields
are preserved. Keys never enter these files.

## Local API

- Binds only `127.0.0.1` (never `0.0.0.0`); random port.
- Bootstrap token is in the URL fragment; `POST /api/auth/exchange` returns an HttpOnly,
  SameSite=Strict cookie and a CSRF token.
- All writes require `X-Morphd-CSRF`; Origin/Host/client address are checked; CORS is not enabled.
- Host bridge calls `/api/host/*` with a per-run token read from the 0600 runtime file.
- Responses use a stable error envelope and a schema version.
- Static UI is served from the package; CSP forbids external scripts/connections and inline
  styles.

## Candidate lifecycle

```
queued → triaging → noop
                └→ running → needs_apply → applied → trial → kept
                      │                        └→ reverted
                      ├→ failed / canceled / paused
```

- Triage is zero-model and records `noop` for successful sessions with no correction/tool loop.
- The candidate is generated in a temporary copy of the current harness. Only declared surfaces
  can be edited; permission/sandbox/approval/hooks/settings/MCP and credentials are rejected.
- Applying snapshots the actual harness, verifies the candidate's base hash, and writes files
  atomically. If the user edited a target after apply, revert fails with `APPLY_CONFLICT` rather
  than overwriting.
- Trial uses the last five same-host/project sessions as a baseline (if available) and reverts on
  hard regressions (corrections/tool loops/security/crash/user feedback). No baseline means hard
  rollback only.
- Snapshots keep the newest 10 per project and pin the earliest for "restore original".

## Engine integration

The consumer analyzer builds `task_contract: 1` evidence (`criteria=[]`, no fabricated checks),
redacts the workspace, and uses the same store/security primitives as the engine. The strict
engine path (task library, shadow evaluation, paired CI, P2/P3 promotion) remains available via
the CLI for enterprise use; the consumer UI does not depend on it.

Codex execution uses native `workspace-write` sandboxing and `--ask-for-approval never`, never
the bypass flags. dsh profiles use fixed `workspace-write + ask`; Claude uses `acceptEdits`,
tool allow/deny, project settings and no bypass. Container/egress remains an opt-in strict mode for external enterprise container runtimes.

## Failure and recovery

- Host exit: current stage is checkpointed, job state becomes `paused`; next host start requeues
  it. Completed model calls are recorded in the budget ledger and are not replayed by design;
  partial proposal temp state is discarded.
- Crash/power loss: same recovery path; no duplicate session/job records (idempotency key is
  host + session + end time).
- Budget before/after each call; unknown price blocks before any call.
- Cancellation keeps evidence and does not count as a failed trial sample.
- The UI shows "disconnected" when the host server is gone; it cannot call the API and never
  resumes work by itself.
