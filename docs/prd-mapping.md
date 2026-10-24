# PRD v0.5 traceability

`PRD-v0.5.md` is the only product PRD. This file maps its requirements to implementation and
evidence.
> **Platform scope (2026-10-23)**: Windows 10/11 x64 only. macOS/Linux requirements in the
> historical PRD are out of scope. The delivered package set is `@harness2-morphd/cli`,
> `@harness2-morphd/tray` and the single `@harness2-morphd/win32-x64` engine package.

Status legend:

- **done** — implemented in this repository and covered by automated tests or browser checks;
- **partial** — implementation exists but one environment-specific acceptance item is
  unverified (host API, packaging/signing, real model);
- **n/a** — not verifiable in this environment; reason and follow-up are listed in
  [`verification.md`](verification.md). `n/a` is never replaced by 0 or a silent pass.

## R1–R9

| # | Requirement | Status | Implementation / evidence |
|---|---|---|---|
| R1 | Session-end auto trigger | partial | `consumer/jobs.py`, `consumer/session_import.py`, dsh idle watcher, Claude `SessionEnd` hook, Codex wrapper exit; 100-session/enqueue idempotency, duplicate-end and incognito tests. Real hosts not installed. |
| R2 | No resident background | done | `consumer/lifecycle.py` stops worker/server on host exit; no cron/systemd/launchd/queue; a single elected worker and singleton tray avoid duplicate host processes. Process-level residue check remains manual. |
| R3 | Lifecycle start/stop with host | partial | wrapper + hook integration, checkpoints/recovery, multi-host worker election, Electron shell. Native dsh plugin API unverified (beta). |
| R4 | Local Web UI + BYOK + model choice | done | React/Vite UI, all pages, provider roles/fallbacks, test-before-save, local Ollama, keyboard/a11y; tests + 35-scan visual matrix. |
| R5 | Humanisation, quiet defaults, caps, feedback | done | Attention manager, trust/appetite policy, pause/vacation/incognito (with start-time guard), weekly cap, quiet hours, feedback down-ranking, inbox, vacation skip summary; tests. |
| R6 | NPM + Electron dual package | partial | Windows-only `packages/cli`, `packages/tray`, `packages/platform/win32-x64`, `consumer/electron`, PowerShell build/release; SHA-256 verification, installers rebuilt, optional release signing wired. Artifacts remain unsigned without certificate secrets; SmartScreen/signing evidence pending. |
| R7 | Host discovery, adaptation, manual folder | done (fake) / partial (real) | `consumer/hosts.py` PATH/npm/pnpm/yarn/common dirs, registry, identity/version checks, backup/rollback/manual validation, re-adaptation; tests with fake dsh. |
| R8 | Runtime tray | partial | Electron tray controller, optional `@harness2-morphd/tray` + `morphd tray` helper, pid/tray singleton, host status grouping, optional dot badge, lifecycle watcher. Windows GUI E2E pending. |
| R9 | NVIDIA Green / Mono visual system | done | design tokens, build-time lint, light/dark, mobile, 125%/150% DPI layout, XL/high-contrast screenshots and 0 axe serious/critical findings. |

## FR-C1–C18

| ID | Area | Status | Notes |
|---|---|---|---|
| C1 | Host lifecycle binding | partial | wrappers/hooks + local server, single-worker election; dsh native plugin beta. |
| C2 | Session-end trigger/idempotency | done | idempotent job key; duplicate SessionEnd preserves job link and trial observations; incognito tombstone; no `session/end` assumption. |
| C3 | No resident background | done | no scheduler/service; local API closes with server; one elected job worker across hosts. |
| C4 | Local Web UI | done | all pages, offline assets, loopback/token/CSRF/CSP, keyboard/reduced motion, confirmed dangerous writes. |
| C5 | BYOK providers | done | OpenAI-compatible/Anthropic/Gemini; keychain/encrypted vault; legacy plaintext migration; test/first-save; fallback; unknown-price block; content refusal not bypassed. |
| C6 | Adaptive analysis/cost | done | triage zero-model; light/adaptive/full; preview cost; pre-call budget guard. |
| C7 | Candidate apply/revert | done | snapshot, diff, only changed files, conflict detection, trial/auto-rollback, restore original; apply/revert require explicit confirmation. |
| C8 | Budget visibility | done | three hard limits, alert ratio, per-provider/kind ledger, last spend, unknown price block, project budgets can only tighten. |
| C9 | Privacy/security | partial | redaction/preview/export/delete, secret-bearing harness files skipped, loopback/CSRF/CSP, changed-file-only candidate scans; real egress/container checks n/a. |
| C10 | Host adapters | partial | dsh/Claude/Codex wrappers; Claude hook; beta/unsupported labels; isolation by host/project; real lifecycle n/a. |
| C11 | Diagnostics/recovery | done | checks, redacted logs, last provider test, safety mode, reset learning, checkpoint recovery. |
| C12 | Enterprise strict mode | partial | CLI gates, P2/P3, container/egress config retained; container target validation n/a. |
| C13 | Humanisation/personalisation | done | trust/appetite, quiet/focus, weekly cap, digest, feedback, incognito, project boundaries, pause recording modes, newbie mode, accessibility settings, vacation summary. |
| C14 | NPM setup | partial | launcher, commands, idempotent setup, manual fallback UI, single win32-x64 optional package, no postinstall, checksum verification. Provenance publishing and optional signing are wired; live OIDC/certificate runs pending. |
| C15 | Electron `.exe` | partial | secure shell/tray/IPC/build config, native picker contract, bundled-engine checksum, NSIS uninstall hook, unsigned Windows NSIS/portable artifacts produced; Authenticode/SmartScreen and GUI E2E pending. |
| C16 | Discovery/adaptation/manual | partial | identity checks, zero-write errors, rollback, multi-version display, path input + system picker, project-only read-only mode, manual config validation. Real hosts n/a. |
| C17 | Runtime tray | partial | Windows code and helper, singleton/pid/heartbeat, optional dot badge, host grouping; GUI acceptance pending. |
| C18 | Visual constraints | done | three colors, ≤10px radii, no grey/gradient/transparency, hard shadows; build lint + 35 visual/a11y scans including 125/150% DPI. |

## Appendix C engine contract

| Contract | Status | Notes |
|---|---|---|
| C.3.1 task_contract:1/checks/splits | done | schema requires `created_at`, provenance accepts any JSON, regex length capped; hidden terms include every marked value. |
| C.3.2 run evidence/idempotency | partial | Engine evidence/idempotency tests pass; failed/interrupted `meta.json` now always carries its required outcome fields as null (C.3.3). Consumer jobs persist JSON checkpoints and reuse the proposal cache after a stopped host, so a resumed analysis re-hits the cache instead of re-billing the model. Per-stage *engine rollout* checkpointing is still not implemented; a crash during a live rollout can repeat that rollout's turns. |
| C.3.3 metrics/statistics | done | failed runs report `delivered`/`correction_messages` as `null`; aggregates exclude unknown values instead of filling 0; an end reason mapped to `other` records an explicit `warnings` entry, and `turns_available` marks when the runtime reported no turn count. |
| C.3.4 budgets/pricing | done | Engine double gate retained; consumer three-tier ledger; evaluation journal is deterministic for resume. |
| C.3.5 evaluation/promotion | done | Strict/enterprise CLI; consumer trial path; promotion no longer writes `.claude/settings.json`. |
| C.3.6 security/redaction/scanning | done | AST/secrets/surface/size/path; nested `urllib.error`/`requests.adapters`, `os.exec*`/`spawn*`, credential and absolute-path reads rejected; module-scope file writes are flagged even as assignment values; workspace hidden-term scan. |
| C.3.7 runtime matrix | partial | Codex native sandbox and resume keep the same non-bypass policy; unknown dsh events are recorded and degraded (`[ERROR] unrecognized dsh event`) instead of being silently dropped. Real CLI evidence missing. |
| C.3.8 scripted replay | done | Fake CLI tests. |
| C.3.9 BYOK proposer | done | Tool + JSON patch modes, root-locked temp tree, budget, cache-after-harvest; `--proposer-channel byok` injects a BYOK proposer into `run_sweep` while the adapter spec uses a legal placeholder channel. Proposer calls honor `max_attempts` (retry reserving/charging budget per real model call). |
| C.3.10 library/report/diagnostics | partial | Engine library is runtime+domain isolated and stale entries quarantine; consumer candidates/experience are project isolated. Cross-device sharing is explicitly disabled. |
| C.4.1 HostAdapter | partial | Python lifecycle interfaces; native dsh plugin beta. |
| C.4.2 local data structures | done | Versioned atomic JSON, 0700/0600, unknown fields preserved, legacy secret fields removed on load. Settings/project validation enforces bool/enum types, `provider/model` role refs, cadence booleans, and project budget/cadence shapes. |
| C.4.3 local API | done | Auth/CSRF/CSP/error codes/array lists/dangerous-write confirmation; `APPLY_CONFLICT` is emitted on apply when a target file was hand-edited (distinct from `CANDIDATE_STALE` on unrelated harness drift); tested. |
| C.4.4 jobs/trial/rollback | done | Triage treats an unknown (`null`) completion signal as a signal rather than a smooth-session noop; per-stage checkpoints, single worker election, trial decisions; auto-apply only when an independent (non-self, non-paused/vacation) baseline exists, otherwise the candidate waits for review; baseline excludes paused/vacation/incognito sessions; no-baseline hard-rollback-only rule; trial window expiry; tests. |
| C.4.5 installer/InstallRecord | done (fake) | Discovery/adapt/uninstall tests, idempotent setup, re-adaptation rollback, NSIS uninstall hook; signing release pending. |
| C.4.6 tray/Electron IPC | partial | Whitelisted preload/validated IPC, singleton tray, heartbeat polling, loopback-only server validation; GUI E2E pending. |
| C.5 build/release | partial | Windows PowerShell build/release, SHA256SUMS, SBOM, release verification, checksum-verified platform engine, tag/version gate, optional Authenticode and OIDC provenance; live certificate/provenance run pending. |
| C.6 Definition of Done | not met | Real host/CLI evidence, signed packages, GUI tray/picker E2E, real model calls and legal review remain. |
| C.7 compatibility/migration | partial | Versions and data migrations; real upgrade batches n/a. |
| C.8 legal/brand/compliance | partial | Privacy/terms/help docs added; legal review, publisher details and Authenticode publisher identity pending. |

## Binding defaults

Advisor + Conservative + auto-apply off + system notifications off + weekly cap 2 + quiet hours
22:00–08:00 + 30-day retention + telemetry off + three colors + radii 4/6/8/10 (max 10) are
implemented as defaults and enforced by settings validation/UI.

## Deliberate deviations / decisions

- Applying a candidate is fixed to its current project. A reviewed “apply to all projects”
  design does not exist yet, so the UI does not expose a control that would silently spread a
  change across projects.
- dsh native plugin integration is beta because the Cordis session-close API is unverified;
  wrapper + conservative idle watcher is used instead of pretending native support.
- Codex bypass flags are removed from the default path; Codex may be labelled beta if no
  non-bypass sandbox is available.
- Tray on the browser path degrades explicitly when `pystray`/`Pillow` or a desktop session is
  unavailable; no fake tray is created.
- Consumer analysis calls the BYOK proposer directly. The strict engine path now also supports
  `--proposer-channel byok`, which injects the same proposer into upstream `run_sweep`.
- Project-only read-only mode is implemented in `consumer/manual.py` and the Hosts page; it
  never writes host config or project files and never auto-triggers.
- Engine rollout recovery relies on run-level idempotency (`.morphd-complete`) plus the consumer
  proposal cache, so a resumed run does not re-bill completed model calls. True per-turn rollout
  checkpointing inside a live engine rollout is not implemented and stays a disclosed `partial`.
- `"expensive"` candidate feedback is recorded and folded into the down-ranking counts, but the
  PRD defines no automatic analysis-depth reduction or local-model switch for it, so none is
  invented; the cost signal is visible in Budget/History instead.

See [`decisions/`](decisions/) for ADRs.
