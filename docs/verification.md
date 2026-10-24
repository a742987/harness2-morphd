# Verification record (2026-10-24, Windows 10/11 x64)

Environment: Windows 11 Pro x64 (10.0.26200), Windows Python 3.12.8, Node 24.12.0,
Windows PowerShell 5.1, Microsoft Edge. The pinned Harness² `1cfe254e8d25` remains vendored
unmodified. WSL is used only for repository editing; every test and build below runs with
Windows binaries. Real hosts are never launched by the test suite: discovery/adaptation tests
use the fake hosts in `tests/fakes/` with an isolated `PATH`, `MORPHD_HOME` and host config
variables.

## Automated results

| Suite | Command | Result |
|---|---|---|
| Engine + consumer | `.venv-win\Scripts\python.exe -X utf8 -m pytest -q` | **138 collected, all pass** |
| Python lint | `.venv-win\Scripts\ruff.exe check morphd tests` | all checks pass |
| Vendor integrity | `powershell scripts\verify_vendor.ps1` | all vendored files OK |
| Web unit | `npm --prefix consumer/web test` | 13 Vitest tests pass |
| Web design lint | `npm --prefix consumer/web run lint` | three-color/radius/lint + tsc pass |
| Web build | `npm --prefix consumer/web run build` | Vite build pass; UI assets emitted |
| Visual/a11y matrix | `npm --prefix consumer/web run test:visual:matrix` | **35 axe scans, 38 screenshots, 13/13 keyboard checks; 0 serious/critical/moderate/minor** |
| NPM CLI | `npm --prefix packages/cli test` | 5/5 pass (fake engine, `.cmd` launcher path) |
| Tray helper | `npm --prefix packages/tray test` | 2/2 pass (attach-only, no host spawn) |
| Electron security | `npm --prefix consumer/electron test` | 5/5 pass (sandbox/CSP/IPC/preload/folder picker/checksum) |
| Electron typecheck/build | `npm --prefix consumer/electron run build` | tsc pass |
| Full workspace test | `npm test` | ruff + 138 pytest + web + CLI + tray + Electron pass |
| Release verification | `powershell scripts\verify_release.ps1 -ReleaseDir release -AllowUnsigned` | **release verification: OK** |

The new regression groups cover:

- dangerous API writes (`apply`, `revert`, `hosts/adapt`, `hosts/uninstall`,
  `privacy/delete`, `settings/reset`, `diagnostics/reset-learning`, provider delete-all)
  rejected without `confirm: true`;
- list endpoints return arrays as documented, and `/api/exports/{name}` returns 404 instead of
  crashing;
- duplicate `SessionEnd` events preserve the existing job link and do not double-count trial
  sessions;
- manual analysis cannot bypass pause/vacation, while a sensitive project remains manual-only
  with a preview;
- project budgets can only tighten, never exceed, the global hard cap;
- the Claude harness surface never includes `.claude/settings*.json` or hooks, and apply writes
  only changed/added files;
- legacy plaintext provider keys are removed from `providers.json` and from public payloads;
- hidden criteria terms are rejected inside workspace files, and nested network imports,
  `os.exec*`/`spawn*`, and credential/absolute-path reads are rejected by the Python scanner;
- failed rollouts report `delivered` and `correction_messages` as `null` rather than fabricated
  `false`/`0`;
- the BYOK proposer bridge returns the upstream `Reply` shape and charges the engine budget guard;
- provider connection tests do not retry, and content-policy refusals are not bypassed by a
  fallback model;
- tab/radio widgets are arrow-key operable; the visual matrix drives the History tabs and the
  full apply/revert flow with the keyboard;
- a `null` (unknown) completion signal is triaged as a signal and analyzed, not treated as a
  smooth-session no-op;
- auto-apply is skipped when there is no independent baseline (only the just-failed session), and
  is allowed once a prior non-paused session exists for the same host+project;
- the trial baseline excludes paused/vacation sessions;
- applying a candidate after a manual edit to its target file raises `APPLY_CONFLICT` and keeps
  the candidate at `needs_apply` (distinct from `CANDIDATE_STALE` on unrelated harness drift);
- failed/interrupted engine runs still write every required `meta.json` outcome field as `null`;
- an unmapped end reason mapped to bucket `other` records an explicit metric `warning`, and
  `turns_available` marks a runtime that reported no turn count;
- unrecognized dsh session events are recorded and degraded rather than silently dropped;
- the BYOK proposer honors `max_attempts` (retries reserve and charge budget per real call);
- the Python scanner rejects a module-scope file write even when it is the value of an assignment
  (`p = Path("f").write_text(...)`) while leaving the same write inside a function allowed;
- stricter settings/project validation rejects non-boolean cadence flags, model refs that are not
  `provider/model`, and project budgets/cadence out of range.

## Windows packaging results (rebuilt 2026-10-24)

| Artifact | Result |
|---|---|
| Engine | PyInstaller `--onefile` → `dist\morphd-engine.exe` (26.6 MB) |
| Engine smoke | `version` prints 0.5.0 and pinned runtime versions from an unpacked executable |
| Packaged local API/UI | `serve --config-only` wrote a loopback `server.json`; Windows `Invoke-WebRequest` returned UI **200** with CSP, `/api/status` **401** without auth, and the process exited on the stop marker |
| Platform package | `harness2-morphd-win32-x64-0.5.0.tgz` (27.6 MB, engine + `SHA256SUMS`) |
| Platform checksum | `sha256sum -c SHA256SUMS` → `bin/morphd-engine.exe: OK` |
| CLI package | `harness2-morphd-cli-0.5.0.tgz` (5 files, verified engine required in production) |
| Tray package | `harness2-morphd-tray-0.5.0.tgz` (5 files) |
| Electron NSIS | `harness2-morphd Setup 0.5.0.exe` (104.2 MB) |
| Electron portable | `harness2-morphd 0.5.0.exe` (104 MB) |
| NSIS uninstall hook | `build/installer.nsh` configures `customUnInstall` to remove host wrappers/hooks through the bundled engine and ask whether to delete local data/keys. **Not executed in this environment.** |
| Signing | **Skipped** — no Authenticode certificate; artifacts are unsigned |
| Checksums | `release\SHA256SUMS-windows.txt`; `verify_release.ps1` checks every published file |
| GitHub Release checksums | Assets are flattened by GitHub; the workflow publishes a regenerated checksum file with basenames |
| SBOM | `release\sbom.cdx.json` (CycloneDX 1.5, 54 components, 5 release artifacts) |

The Electron shell verifies the bundled engine's SHA-256 before launch; the CLI and tray helper
verify the platform package's `SHA256SUMS`. Production engine resolution no longer falls back to
`python -m morphd`; Python source use requires `MORPHD_ALLOW_PYTHON_ENGINE=1` or an explicit
`MORPHD_PYTHON`.

## Covered consumer paths

- Settings defaults, validation, trust/appetite policy, autopilot confirmation, digest modes,
  custom auto-apply window, pause-recording modes and snapshot retention.
- Credential Manager fallback encrypted vault round-trip; no plaintext key in the vault file;
  legacy plaintext provider fields are migrated/removed.
- BYOK OpenAI-compatible completion/test; tool-mode connection probe; cost/usage; fallback
  recording; content-policy refusal is not retried or silently switched.
- Three-tier budget reservations/charges/hard stops/unknown price; project budgets cannot raise
  the global cap; a pre-call guard stops before the next model call.
- Quiet hours, weekly cap, down-ranking, pause/resume, vacation skip summary, incognito
  tombstone and pre-set next-session incognito with a start-time guard.
- Host discovery/adapt/uninstall with a fake dsh; `.cmd` wrapper creation; identity/version
  checks; backup/restore; re-adaptation from original state; zero writes on invalid directories.
- Claude project/user hook installation with JSON validation and rollback; hook commands carry
  the installer's `MORPHD_HOME`; project-level hooks are preferred when a project root is selected.
- dsh session normalisation, corrections, tool loops, end reasons; Claude unknown top-level
  events are recorded as unsupported and do not enter analysis; Codex unknown items are recorded
  and degraded.
- Candidate generation via fake BYOK provider, policy scan, diff, snapshot, apply, revert,
  conflict detection, trial state; apply writes only changed files; revert restores only the
  files the candidate touched.
- 100 unique sessions enqueue with no duplicates; duplicate session/end is idempotent; trial
  observations ignore duplicate/blocked sessions; a single elected worker processes the shared
  queue across multiple host processes.
- Manual analysis preview includes a cost estimate; sensitive-project analysis requires manual
  action; reset learning keeps sessions/snapshots/keys.
- Local API auth exchange, CSRF rejection, Host-header rejection, CSP, static UI, 401 without
  auth, dangerous-write confirmation and array list responses.
- Codex argv uses `--sandbox workspace-write --ask-for-approval never`; the resume path keeps the
  same sandbox/approval policy; bypass flags are removed.
- Engine default isolation is host-native; container/egress is opt-in strict mode.
- Single runtime tray: a pid file + spawn lock ensures one NPM helper; Python/Electron trays
  disappear with the last host; a subtle dot badge is optional and never numeric/flashing.
- Silent-first-tab skip link, arrow-key tabs/radio cards, focus rings, reduced motion, font
  scaling, high contrast, light/dark surfaces, 125%/150% DPI layout and axe AA checks.

## Visual evidence

Screenshots in [`docs/evidence/ui/`](evidence/ui/) were regenerated by the matrix:

- all 10 pages in light and dark modes at 100%;
- `home-125.png`, `home-150.png`, `hosts-125/150.png`, `history-125/150.png`,
  `models-125/150.png` with the CSS viewport reduced to simulate Windows display scaling;
- `home-mobile.png` (375 px), `home-xl-high.png` (xlarge font + high contrast);
- onboarding host/model/budget, candidate modal light/dark and keyboard flows;
- `candidate-modal-keyboard.png`, `keyboard-nav-focus.png`, `keyboard-revert-confirm.png`;
- machine-readable [`axe-report.json`](evidence/axe-report.json) and
  [`visual-a11y-summary.md`](evidence/visual-a11y-summary.md).

## Not verified (explicit n/a / beta)

| Item | Why | Follow-up |
|---|---|---|
| Real dsh/Claude/Codex lifecycle and session-end APIs | Hosts are not installed/authenticated in this environment. The dsh idle watcher, Claude hook and Codex wrapper have fake-CLI coverage only. | Run the compatibility matrix on pinned hosts; record raw events. |
| Native dsh Cordis plugin | Session-close API not evidenced; wrapper is used instead. | Validate plugin API on dsh 0.2.1-rc.2; otherwise keep beta. |
| Real model calls through BYOK | No user keys/credentials available; fake provider only. | Test each protocol against official endpoints, capture usage/errors. |
| Authenticode, SmartScreen, clean-machine install/uninstall | No code-signing certificate or clean Windows VM. | Sign in release CI; run install/uninstall/SmartScreen matrix. |
| npm provenance/2FA | Local automation token used; not a CI OIDC provenance run. | Publish from GitHub Actions with provenance/trusted publishing. |
| Tray GUI, high DPI/dark mode, multi-host aggregation | No GUI automation for the Windows notification area in this environment. The singleton/pid/heartbeat logic is unit-level only. | Run the tray checklist; verify no orphan tray after crash, one tray for two hosts. |
| Container/egress enforcement | Depends on an external container/firewall. | Validate with an external container runtime and a network deny test. |
| Real 100-session host run | Fake sessions only. | Run the 100-session acceptance on a real host and keep logs. |
| Enterprise P2/P3 gate on real tasks | No real task library/team. | Build a task set and run M1/M2 gates. |
| Electron GUI E2E (native picker, close-to-tray, NSIS uninstall hook) | Launching windowed GUI automation is out of scope for this agent session. The security unit test asserts the picker/IPC/preload contract; the NSIS hook is built but not executed. | Run Playwright-Electron + a clean VM uninstall test. |
| “Apply to all projects” scope | Not implemented; applying is fixed to the candidate's current project so a change cannot silently spread across projects. | Add a reviewed multi-project apply/revert design before enabling it. |
| Windows Credential Manager on a real user profile | Tests force the encrypted-vault fallback. | Run a manual key save/read/delete test on a clean Windows profile. |
| Real native folder picker via the browser path | Headless/WSL cannot render the OS dialog. Electron path is contract-tested. | Manual click-through on Windows desktop. |

`n/a` items are product/release blockers for claiming C.6 Definition of Done; they are not
silently treated as passing. See [`prd-mapping.md`](prd-mapping.md).

## Reproduce

```powershell
.venv-win\Scripts\ruff.exe check morphd tests
.venv-win\Scripts\python.exe -X utf8 -m pytest -q
npm --prefix consumer/web run lint
npm --prefix consumer/web test
npm --prefix consumer/web run build
npm --prefix consumer/web run test:visual:matrix
npm --prefix packages/cli test
npm --prefix packages/tray test
npm --prefix consumer/electron run build
npm --prefix consumer/electron test
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify_vendor.ps1

# build engine, npm tarballs, unsigned Electron installers, SBOM and checksums
npm run release

# verify every published file, SBOM and signing status
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify_release.ps1 -AllowUnsigned
```
