# Release notes — v0.5.0 (Windows 10/11 x64)

## Added

- Local-first consumer layer: host discovery/adaptation, session observation, in-host job
  runner, three-tier budget, attention manager, privacy center, local API and Web UI.
- Three-color NVIDIA Green/Mono design system with build-time lint and Playwright/axe evidence.
- BYOK providers (OpenAI-compatible, Anthropic, Gemini), tool/patch proposer, encrypted vault
  fallback, connection testing and fallbacks.
- Candidate snapshots, apply/revert conflict detection, trials, automatic rollback and
  restore-original.
- NPM launcher (`setup`/`doctor`/`update`/`uninstall`), optional Windows tray helper, Electron
  shell with sandbox/preload/validated IPC/tray, and a single `@harness2-morphd/win32-x64`
  platform package.
- Windows-native store locking, `.cmd` host wrappers and process invocation for npm-installed
  hosts.
- PowerShell build/release pipeline: PyInstaller engine, SHA256SUMS, npm pack, Electron
  NSIS/portable installers and a combined checksum file.

## Changed (breaking)

- **Platform support is now Windows 10/11 x64 only.** macOS/Linux platform packages, installers,
  POSIX runtime branches and the Linux container deployment were removed. Builds and tests run
  on Windows.
- NPM and Electron are the only distribution paths. `@harness2-morphd/cli` and
  `@harness2-morphd/tray` depend only on `@harness2-morphd/win32-x64`.
- Codex no longer defaults to `--dangerously-bypass-approvals-and-sandbox` or hook bypass;
  it uses native `workspace-write` sandbox and `never` approvals.
- Engine real-run isolation is opt-in (`MORPHD_CONTAINER_REQUIRED=1`) with host-native
  sandboxing as the consumer default. Container/egress now requires an external container
  runtime; no image is shipped.
- Cron/nightly scheduling and the residential trigger plugin were removed from the product
  path; scheduling is tied to host lifecycle (enterprise may use external schedulers).
- Engine version is now `0.5.0`; local API is v1.
- Product and published package names are now `harness2-morphd` (npm scope
  `@harness2-morphd/*`, PyPI distribution `harness2-morphd`, Electron `productName`).
  Technical identifiers keep the short form: the `morphd` Python package and console
  command, `MORPHD_*` environment variables, the `%USERPROFILE%\.morphd` data directory,
  `morphd:*` IPC channels and the `morphd-engine.exe` binary.

## Packaging / release status

- Release artifacts: npm tarballs for `win32-x64`, `cli` and `tray`; Electron NSIS and portable
  `.exe`; `SHA256SUMS-windows.txt`.
- **Artifacts are unsigned.** Authenticode, SmartScreen reputation, code-signing verification
  and signed updates are not implemented in this release.
- npm publication and GitHub Release are performed from this repository with the version
  `0.5.0`.

## Known limitations

- Real host/model evidence, Authenticode signing, tray GUI matrix and container/egress
  validation are pending. See `docs/verification.md`.
- dsh native plugin integration is beta; wrapper + idle fallback is used.
