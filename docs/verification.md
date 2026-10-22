# Verification record (2026-10-22)

Environment: WSL2, Python 3.12.15, Node 24.12.0 (Windows interop), pinned Harness²
`1cfe254e8d25` vendored unmodified, fake CLIs in `tests/fakes/`, Edge 2026 via Playwright.

## Automated results

| Suite | Command | Result |
|---|---|---|
| Engine + consumer | `.venv/bin/pytest -q` | **85 collected, all pass** |
| Python lint | `.venv/bin/ruff check morphd tests` | all checks pass |
| Vendor integrity | `sh scripts/verify_vendor.sh` | all vendored files OK |
| Web unit | `npm --prefix consumer/web test` | 4 Vitest tests pass |
| Web design lint | `node consumer/web/scripts/check-design.mjs` | three-color/radius/lint pass |
| Web typecheck/build | `npm --prefix consumer/web run build` | tsc + Vite build pass |
| NPM CLI | `npm --prefix packages/cli test` | 5/5 pass (fake engine, Windows batch shim) |
| Tray helper | `npm --prefix packages/tray test` | 2/2 pass (attach-only, no host spawn) |
| Electron security | `npm --prefix consumer/electron test` | 3/3 pass (sandbox/CSP/IPC/preload source assertions) |
| Electron typecheck | `npm --prefix consumer/electron run build` | tsc pass |
| Visual/a11y | `node consumer/web/scripts/visual-check.mjs <url> <out> [onboarding]` | 19 screenshots pass; no serious/critical axe violations |
| Wheel smoke | clean venv + `pip install morphd-0.5.0.whl`, `morphd version`, `morphd serve --config-only` | pass without Harness² installed; UI assets present |

### Covered consumer paths

- Settings defaults, validation, trust/appetite policy, autopilot confirmation, digest modes.
- Keychain fallback encrypted vault round-trip; no plaintext key in the vault file.
- BYOK OpenAI-compatible completion/test; tool-mode connection probe; cost/usage.
- Three-tier budget reservations/charges/hard stops/unknown price.
- Quiet hours, weekly cap, down-ranking, pause/resume, incognito tombstone.
- Host discovery/adapt/uninstall with a fake dsh; wrapper creation; backup/restore.
- dsh session normalisation, corrections, tool loops, end reasons.
- Candidate generation via fake BYOK provider, policy scan, diff, snapshot, apply, revert,
  conflict detection, trial state.
- 100 unique sessions enqueue with no duplicates; duplicate session/end is idempotent.
- Manual analysis preview includes a cost estimate; reset learning keeps sessions/snapshots.
- Local API auth exchange, CSRF rejection, Host-header rejection, CSP, static UI, 401 without auth.
- Codex argv now uses `--sandbox workspace-write --ask-for-approval never`; bypass removed.
- Engine default isolation now host-native; container/egress is opt-in strict mode.

### Visual evidence

Screenshots in [`docs/evidence/ui/`](evidence/ui/):

- `home-light.png`, `home-dark.png`, `home-mobile.png`
- `home-125.png`, `home-150.png` (DPI 125%/150%), `home-xl-high.png` (XL font + high contrast)
- `onboarding-host-light.png`, `onboarding-model-light.png`, `onboarding-budget-light.png`
- `candidate-modal-light.png` (diff, safety, feedback, apply)
- `hosts-light.png`, `models-light.png`, `learning-light.png`, `budget-light.png`,
  `history-light.png`, `privacy-light.png`, `diagnostics-light.png`, `help-light.png`,
  `advanced-light.png`

The Playwright run:
- executes real auth exchange and API calls against a seeded local home;
- captures `console.error`/`pageerror` (none);
- injects axe-core and fails on serious/critical violations (none);
- checks light/dark, 375px mobile, 125/150% device scale factors, XL font/high contrast,
  the three onboarding steps, and the candidate detail modal.
- Windows Python additionally ran `tests/test_consumer.py`: **22/22 pass**, including the
  Windows `.cmd` host shim and `msvcrt` file locking path.

Focus/keyboard: all controls are native buttons/inputs/selects; a modal traps Tab and restores
focus; focus rings are black + green; the design lint forbids transparency/gradients/radius 0.

## Not verified (explicit n/a)

| Item | Why | Follow-up |
|---|---|---|
| Real dsh/Claude/Codex lifecycle and session-end APIs | Hosts are not installed/authenticated in this environment. The dsh idle watcher, Claude hook and Codex wrapper are implemented but have only fake-CLI coverage. | Run the compatibility matrix on pinned hosts; record raw events. |
| Native dsh Cordis plugin | Session-close API not evidenced; wrapper is used instead. | Validate plugin API on dsh 0.2.1-rc.2; otherwise keep beta. |
| Real model calls through BYOK | No user keys/credentials available; fake provider only. | Test each protocol against official endpoints, capture usage/errors. |
| Windows `.exe`, Authenticode, SmartScreen | Packaging/signing infrastructure is not present here. | CI build on Windows, sign, install/uninstall matrix. |
| npm publish/provenance/2FA, platform binaries | Package names not registered; no signed engine artifacts. | Register scope, build/sign platform packages, publish with provenance. |
| Tray GUI on Windows/macOS/Linux | No GUI automation for plasma/AppIndicator/menu-bar in this environment. | Run the tray checklist on each OS; no orphan tray after crash. |
| Container/egress enforcement | Depends on the target container/firewall. | Validate in `deploy/container` with a network deny test. |
| Real 100-session host run | Fake sessions only. | Run the 100-session acceptance on a real host and keep logs. |
| Enterprise P2/P3 gate on real tasks | No real task library/team. | Build a task set and run M1/M2 gates. |
| Electron-native folder picker / project-only manual mode | The browser path uses the engine/system picker; project-only read-only analysis is not implemented. | Add IPC folder dialog and a read-only analysis path, or document as unsupported. |

`n/a` items are product/release blockers for claiming C.6 Definition of Done; they are not
silently treated as passing. See [`prd-mapping.md`](prd-mapping.md).

## Reproduce

```sh
.venv/bin/ruff check morphd tests
.venv/bin/pytest -q
npm --prefix consumer/web run lint
npm --prefix consumer/web test
npm --prefix consumer/web run build
npm --prefix packages/cli test
npm --prefix consumer/electron run build
npm --prefix consumer/electron test

# visual (requires a running local UI and Edge)
morphd serve --open &   # read the printed URL
node consumer/web/scripts/visual-check.mjs "$URL" "$PWD/docs/evidence/ui"
```
