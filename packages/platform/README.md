# Platform package

This repository ships one platform package:

- `@harness2-morphd/win32-x64` — Windows 10/11 x64

The published tarball contains the prebuilt engine at `bin/morphd-engine.exe`, the bundled Web UI
assets, `SHA256SUMS`, `LICENSE` and `NOTICE`. `@harness2-morphd/cli` and
`@harness2-morphd/tray` resolve it through `optionalDependencies`; they never download an
executable from an arbitrary URL and never run a postinstall script.

## Release checklist

1. Build the engine with `scripts/build_engine.ps1` (PyInstaller `--onefile`).
2. Run `scripts/build_windows_package.ps1` to copy the engine to `bin/morphd-engine.exe` and
   write `SHA256SUMS`.
3. Sign the binary with Authenticode when a certificate is available. Until then, publish it as
   an explicitly unsigned preview and record the missing signature in the release notes.
4. Publish the platform package with npm provenance, then publish `cli` and `tray` with the same
   version and `optionalDependencies`.
5. Verify a clean Windows 10/11 x64 install with `npx @harness2-morphd/cli@latest doctor`.
