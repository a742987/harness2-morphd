# Release notes — v0.5.0 (consumer layer)

## Added

- Local-first consumer layer: host discovery/adaptation, session observation, in-host job
  runner, three-tier budget, attention manager, privacy center, local API and Web UI.
- Three-color NVIDIA Green/Mono design system with build-time lint and Playwright/axe evidence.
- BYOK providers (OpenAI-compatible, Anthropic, Gemini), tool/patch proposer, encrypted vault
  fallback, connection testing and fallbacks.
- Candidate snapshots, apply/revert conflict detection, trials, automatic rollback and
  restore-original.
- NPM launcher (`setup`/`doctor`/`update`/`uninstall`), optional tray helper, Electron shell
  with sandbox/preload/validated IPC/tray, platform package manifests and build script.
- Windows-compatible atomic store locking and `.cmd` host wrappers.

## Changed (breaking)

- Codex no longer defaults to `--dangerously-bypass-approvals-and-sandbox` or hook bypass;
  it uses native `workspace-write` sandbox and `never` approvals.
- Engine real-run isolation is opt-in (`MORPHD_CONTAINER_REQUIRED=1`) with host-native
  sandboxing as the consumer default.
- Cron/nightly scheduling and the residential trigger plugin were removed from the product
  path; scheduling is tied to host lifecycle (enterprise may use external schedulers).
- Engine version is now `0.5.0`; local API is v1.
- Product and published package names are now `harness2-morphd` (npm scope
  `@harness2-morphd/*`, PyPI distribution `harness2-morphd`, Electron `productName`).
  Technical identifiers keep the short form: the `morphd` Python package and console
  command, `MORPHD_*` environment variables, the `~/.morphd` data directory, `morphd:*`
  IPC channels and the `morphd-engine` binary.

## Known limitations

- Real host/model evidence, signed Windows/macOS/Linux packages, npm publication, tray GUI
  matrix and container/egress validation are pending. See `docs/verification.md`.
- dsh native plugin integration is beta; wrapper + idle fallback is used.
