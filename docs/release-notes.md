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
- Dangerous writes (`apply`, `revert`, adapt/uninstall, delete/reset, provider delete-all)
  require an explicit `confirm: true` in the local API.
- Single-worker election and a singleton tray helper keep multiple concurrently running hosts
  on one job queue and one tray; a small optional dot badge replaces numeric/flashing badges.
- Candidate apply writes only the changed/added files; revert restores only those files and
  never rewrites Claude settings/hooks or unrelated user edits.
- Engine security: hidden terms are scanned in workspace files; nested network imports,
  `os.exec*`/`spawn*`, credential/absolute-path reads and risk terms are rejected; failed runs
  report outcome fields as `null`.
- `morphd run --proposer-channel byok` injects the BYOK proposer into upstream `run_sweep`
  with the engine budget guard; the adapter uses a legal placeholder channel.
- The launcher verifies the platform engine SHA-256, refuses production Python fallback,
  treats repeated `setup` idempotently and can open the UI when no host was found for manual
  folder selection. The NSIS uninstaller is configured to run the bundled engine to remove host wrappers/hooks; execution is pending a clean-VM test.
- Visual evidence now includes 125%/150% simulated display scaling for Home, Hosts, History and
  Models, plus a 35-scan axe matrix and keyboard apply/revert flows.
- Legacy hand-edited plaintext provider fields are removed on load and migrated into the
  keychain/vault where possible.
- Vacation mode reports how many sessions were skipped without re-running or charging them.

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
- **Artifacts are unsigned by default.** Release CI can sign them when Authenticode certificate
  secrets are configured; SmartScreen reputation and signed-update evidence remain pending.
- npm publication with provenance and GitHub Release are driven from this repository for the
  tag matching the package version `0.5.0`.

## Known limitations

- Real host/model evidence, a real certificate-signed release, tray GUI matrix and
  container/egress validation are pending. See `docs/verification.md`.
- dsh native plugin integration is beta; wrapper + idle fallback is used.
- Applying a candidate is fixed to its current project; “apply to all projects” awaits a
  reviewed multi-project apply/revert design.
- Per-stage rollout checkpointing in the strict engine is not implemented; the consumer path
  reuses proposal caches and persisted job checkpoints after a host stop.
