# PRD v0.5 traceability

`PRD-v0.5.md` is the only product PRD. This file maps its requirements to implementation and
evidence. Status legend:

- **done** — implemented in this repository and covered by automated tests or browser checks;
- **partial** — implementation exists but one environment-specific acceptance item is
  unverified (host API, packaging/signing, real model);
- **n/a** — not verifiable in this environment; reason and follow-up are listed in
  [`verification.md`](verification.md). `n/a` is never replaced by 0 or a silent pass.

## R1–R9

| # | Requirement | Status | Implementation / evidence |
|---|---|---|---|
| R1 | Session-end auto trigger | partial | `consumer/jobs.py`, `consumer/session_import.py`, dsh idle watcher, Claude `SessionEnd` hook, Codex wrapper exit; 100-session/incognito tests. Real hosts not installed. |
| R2 | No resident background | done | `consumer/lifecycle.py` stops worker/server on host exit; no cron/systemd/launchd/queue; server.json removed. Process-level check is manual (see verification). |
| R3 | Lifecycle start/stop with host | partial | wrapper + hook integration, checkpoints and recovery; Electron shell. Native dsh plugin API unverified (beta). |
| R4 | Local Web UI + BYOK + model choice | done | React/Vite UI, all settings pages, provider roles/fallbacks, test-before-save, local Ollama; tests + visual smoke. |
| R5 | Humanisation, quiet defaults, caps, feedback | done | Attention manager, trust/appetite policy, pause/vacation/incognito, weekly cap, quiet hours, feedback down-ranking, inbox; tests. |
| R6 | NPM + Electron dual package | partial | `packages/cli`, `packages/tray`, `consumer/electron`, platform manifests/build script. Signed artifacts/npm publish/SmartScreen are release-environment work. |
| R7 | Host discovery, adaptation, manual folder | done (fake) / partial (real) | `consumer/hosts.py` PATH/npm/common dirs, identity/version checks, backup/rollback/manual validation; tests with fake dsh. |
| R8 | Runtime tray | partial | Electron tray controller, optional `@morphd/tray` + `morphd tray` helper, single instance, lifecycle watcher. GUI E2E on Windows/macOS/Linux pending. |
| R9 | NVIDIA Green / Mono visual system | done | design tokens, build-time lint, light/dark, mobile, 125%/150% DPI, XL/high-contrast screenshots and axe checks. |

## FR-C1–C18

| ID | Area | Status | Notes |
|---|---|---|---|
| C1 | Host lifecycle binding | partial | wrappers/hooks + local server; dsh native plugin beta. |
| C2 | Session-end trigger/idempotency | done | idempotent job key; 100 sessions; incognito tombstone; no `session/end` assumption. |
| C3 | No resident background | done | no scheduler/service; local API closes with server. |
| C4 | Local Web UI | done | all pages, offline assets, loopback/token/CSRF/CSP, keyboard/reduced motion. |
| C5 | BYOK providers | done | OpenAI-compatible/Anthropic/Gemini; keychain/encrypted vault; test/first-save; fallback; unknown-price block. |
| C6 | Adaptive analysis/cost | done | triage zero-model; light/adaptive/full; precheck. |
| C7 | Candidate apply/revert | done | snapshot, diff, conflict detection, trial/auto-rollback, restore original. |
| C8 | Budget visibility | done | three hard limits, alert ratio, per-kind ledger, unknown price block. |
| C9 | Privacy/security | partial | redaction/preview/export/delete, loopback/CSRF/CSP, candidate scans; real egress/container checks n/a. |
| C10 | Host adapters | partial | dsh/Claude/Codex wrappers; Claude hook; beta/unsupported labels; isolation by host/project. |
| C11 | Diagnostics/recovery | done | checks, redacted logs, safety mode, reset learning, checkpoint recovery. |
| C12 | Enterprise strict mode | partial | CLI gates, P2/P3, container/egress config retained; container target validation n/a. |
| C13 | Humanisation/personalisation | done | trust/appetite, quiet/focus, weekly cap, digest, feedback, incognito, project boundaries, newbie mode, accessibility settings. |
| C14 | NPM setup | partial | launcher, commands, optional platform packages, no postinstall; publishing/signing pending. |
| C15 | Electron `.exe` | partial | secure shell/tray/IPC/build config; signed Windows installer not produced in this environment. |
| C16 | Discovery/adaptation/manual | partial | identity checks, zero-write errors, rollback, multi-version display, path input + system picker via engine. Project-only read-only mode and an Electron-native dialog are not yet implemented. |
| C17 | Runtime tray | partial | code and helper; OS matrix GUI acceptance pending. |
| C18 | Visual constraints | done | three colors, ≤10px radii, no grey/gradient/transparency, hard shadows; build lint + 19 visual/a11y screenshots including 125/150% DPI. |

## Appendix C engine contract

| Contract | Status | Notes |
|---|---|---|
| C.3.1 task_contract:1/checks/splits | done | Existing schema/tests unchanged. |
| C.3.2 run evidence/idempotency | done | Engine tests; consumer jobs use JSON checkpoints. |
| C.3.3 metrics/statistics | done | Engine; consumer uses session signals. |
| C.3.4 budgets/pricing | done | Engine double gate retained; consumer three-tier ledger. |
| C.3.5 evaluation/promotion | done | Strict/enterprise CLI; consumer trial path implemented. |
| C.3.6 security/redaction/scanning | done | AST/secrets/surface/size/path; consumer policy stricter. |
| C.3.7 runtime matrix | partial | Codex now native sandbox; real CLI evidence missing. |
| C.3.8 scripted replay | done | Fake CLI tests. |
| C.3.9 BYOK proposer | done | Tool + JSON patch modes, root-locked temp tree, budget, cache. |
| C.3.10 library/report/diagnostics | done | Engine; consumer keeps host/project isolation. |
| C.4.1 HostAdapter | partial | Python lifecycle interfaces; native dsh plugin beta. |
| C.4.2 local data structures | done | Versioned atomic JSON, 0700/0600, unknown fields preserved. |
| C.4.3 local API | done | Auth/CSRF/CSP/error codes; tested. |
| C.4.4 jobs/trial/rollback | done | Triage, checkpoints, trial decisions; tests. |
| C.4.5 installer/InstallRecord | done (fake) | Discovery/adapt/uninstall tests; signing release pending. |
| C.4.6 tray/Electron IPC | partial | Whitelist preload/validated IPC; GUI E2E pending. |
| C.5 build/release | partial | Local builds/tests; signed artifacts, SBOM/provenance release pipeline pending. |
| C.6 Definition of Done | not met | Real host/CLI evidence, signed packages, npm publication, DPI/accessibility audits, legal review remain. |
| C.7 compatibility/migration | partial | Versions and data migrations; real upgrade batches n/a. |
| C.8 legal/brand/compliance | partial | Privacy/terms/help docs added; legal review and publisher details pending. |

## Binding defaults

Advisor + Conservative + auto-apply off + system notifications off + weekly cap 2 + quiet hours
22:00–08:00 + 30-day retention + telemetry off + three colors + radii 4/6/8/10 (max 10) are
implemented as defaults and enforced by settings validation/UI.

## Deliberate deviations / decisions

- The consumer path uses the local engine APIs directly and keeps the strict Harness² sweep for
  enterprise; it does not require users to import a task split (documented in ADR-0001).
- dsh native plugin integration is beta because the Cordis session-close API is unverified;
  wrapper + conservative idle watcher is used instead of pretending native support.
- Codex bypass flags are removed from the default path; Codex may be labelled beta if no
  non-bypass sandbox is available.
- Tray on the browser path degrades explicitly when `pystray`/`Pillow` or a desktop session is
  unavailable; no fake tray is created.

See [`decisions/`](decisions/) for ADRs.
