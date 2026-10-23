# Verification record (2026-10-23, Windows 10/11 x64)

Environment: Windows 11 Pro x64 (10.0.26200), Windows Python 3.12.8, Node 24.12.0,
Windows PowerShell 5.1, Edge. The pinned Harness² `1cfe254e8d25` remains vendored
unmodified. The full suite and packaging run on Windows; WSL is used only for repository
editing.

## Automated results

| Suite | Command | Result |
|---|---|---|
| Engine + consumer | `.venv-win\Scripts\python.exe -X utf8 -m pytest -q` | **85 collected, all pass** |
| Python lint | `.venv-win\Scripts\ruff.exe check morphd tests` | all checks pass |
| Vendor integrity | `powershell scripts\verify_vendor.ps1` | all vendored files OK |
| Web unit | `npm --prefix consumer/web test` | 4 Vitest tests pass |
| Web design lint | `npm --prefix consumer/web run lint` | three-color/radius/lint + tsc pass |
| Web build | `npm --prefix consumer/web run build` | Vite build pass; UI assets emitted |
| NPM CLI | `npm --prefix packages/cli test` | 5/5 pass (fake engine, `.cmd` launcher path) |
| Tray helper | `npm --prefix packages/tray test` | 2/2 pass (attach-only, no host spawn) |
| Electron security | `npm --prefix consumer/electron test` | 3/3 pass (sandbox/CSP/IPC/preload assertions) |
| Electron typecheck/build | `npm --prefix consumer/electron run build` | tsc pass |
| Full workspace test | `npm test` | ruff + 85 pytest + web + CLI + tray + Electron pass |

### Windows packaging results

| Artifact | Result |
|---|---|
| Engine | PyInstaller `--onefile` → `dist\morphd-engine.exe` (26.5 MB) |
| Engine smoke | `version` and `consumer-doctor` pass; UTF-8 Chinese output verified |
| Packaged local API/UI | `serve --config-only` → UI HTTP 200, API version 1, app 0.5.0 |
| Platform package | `harness2-morphd-win32-x64-0.5.0.tgz` (27.5 MB, engine + UI + SHA256SUMS) |
| CLI package | `harness2-morphd-cli-0.5.0.tgz` (5 files) |
| Tray package | `harness2-morphd-tray-0.5.0.tgz` (5 files) |
| Electron NSIS | `harness2-morphd Setup 0.5.0.exe` (104.1 MB) |
| Electron portable | `harness2-morphd 0.5.0.exe` (103.9 MB) |
| Electron smoke | Unpacked app starts bundled engine; UI HTTP 200; engine child process observed |
| Signing | **Skipped** — no Authenticode certificate; artifacts are unsigned |
| Checksums | `release\SHA256SUMS-windows.txt`; `sha256sum -c` passes for all 5 artifacts |

The Electron smoke found and fixed a real packaging regression: ESM imports in
`dist/main.js` needed `.js` extensions. The rebuilt app now starts the bundled engine and
serves the UI. The Windows code-page issue in `consumer-doctor` was also fixed with explicit
UTF-8 stdio.

### Covered consumer paths

- Settings defaults, validation, trust/appetite policy, autopilot confirmation, digest modes.
- Credential Manager fallback encrypted vault round-trip; no plaintext key in the vault file.
- BYOK OpenAI-compatible completion/test; tool-mode connection probe; cost/usage.
- Three-tier budget reservations/charges/hard stops/unknown price.
- Quiet hours, weekly cap, down-ranking, pause/resume, incognito tombstone.
- Host discovery/adapt/uninstall with a fake dsh; `.cmd` wrapper creation; backup/restore.
- dsh session normalisation, corrections, tool loops, end reasons.
- Candidate generation via fake BYOK provider, policy scan, diff, snapshot, apply, revert,
  conflict detection, trial state.
- 100 unique sessions enqueue with no duplicates; duplicate session/end is idempotent.
- Manual analysis preview includes a cost estimate; reset learning keeps sessions/snapshots.
- Local API auth exchange, CSRF rejection, Host-header rejection, CSP, static UI, 401 without auth.
- Codex argv uses `--sandbox workspace-write --ask-for-approval never`; bypass removed.
- Engine default isolation is host-native; container/egress is opt-in strict mode.

## Visual evidence

Screenshots in [`docs/evidence/ui/`](evidence/ui/) were captured with Playwright + Edge on the
existing UI. The Windows-only refactor changed help/placeholder text but not layout or design
tokens; the design lint and web build pass. A fresh screenshot/axe matrix was not rerun in this
packaging pass.

- `home-light.png`, `home-dark.png`, `home-mobile.png`
- `home-125.png`, `home-150.png`, `home-xl-high.png`
- `onboarding-host-light.png`, `onboarding-model-light.png`, `onboarding-budget-light.png`
- `candidate-modal-light.png`
- `hosts-light.png`, `models-light.png`, `learning-light.png`, `budget-light.png`,
  `history-light.png`, `privacy-light.png`, `diagnostics-light.png`, `help-light.png`,
  `advanced-light.png`

## Not verified (explicit n/a)

| Item | Why | Follow-up |
|---|---|---|
| Real dsh/Claude/Codex lifecycle and session-end APIs | Hosts are not installed/authenticated in this environment. The dsh idle watcher, Claude hook and Codex wrapper have fake-CLI coverage only. | Run the compatibility matrix on pinned hosts; record raw events. |
| Native dsh Cordis plugin | Session-close API not evidenced; wrapper is used instead. | Validate plugin API on dsh 0.2.1-rc.2; otherwise keep beta. |
| Real model calls through BYOK | No user keys/credentials available; fake provider only. | Test each protocol against official endpoints, capture usage/errors. |
| Authenticode, SmartScreen, clean-machine install/uninstall | No code-signing certificate or clean Windows VM. | Sign in release CI; run install/uninstall/SmartScreen matrix. |
| npm provenance/2FA | Local automation token used; not a CI OIDC provenance run. | Publish from GitHub Actions with provenance/trusted publishing. |
| Tray GUI | No GUI automation for the Windows notification area in this environment. | Run the tray checklist; verify no orphan tray after crash. |
| Container/egress enforcement | Depends on an external container/firewall. | Validate with an external container runtime and a network deny test. |
| Real 100-session host run | Fake sessions only. | Run the 100-session acceptance on a real host and keep logs. |
| Enterprise P2/P3 gate on real tasks | No real task library/team. | Build a task set and run M1/M2 gates. |
| Electron-native folder picker / project-only manual mode | The browser path uses the engine/system picker; project-only read-only analysis is not implemented. | Add IPC folder dialog and a read-only analysis path, or document as unsupported. |

`n/a` items are product/release blockers for claiming C.6 Definition of Done; they are not
silently treated as passing. See [`prd-mapping.md`](prd-mapping.md).

## Reproduce

```powershell
.venv-win\Scripts\ruff.exe check morphd tests
.venv-win\Scripts\python.exe -X utf8 -m pytest -q
npm --prefix consumer/web run lint
npm --prefix consumer/web test
npm --prefix consumer/web run build
npm --prefix packages/cli test
npm --prefix packages/tray test
npm --prefix consumer/electron run build
npm --prefix consumer/electron test
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify_vendor.ps1

# build engine, npm tarballs, unsigned Electron installers and checksums
npm run release
```
