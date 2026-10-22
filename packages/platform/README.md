# Platform packages

Each directory is the manifest for one npm optional platform package:

- `@morphd/linux-x64`
- `@morphd/linux-arm64`
- `@morphd/darwin-x64`
- `@morphd/darwin-arm64`
- `@morphd/win32-x64`

The published tarball contains a signed/checksummed engine binary under `bin/` plus the
Web UI assets shipped inside the engine package. `@morphd/cli` resolves the package through
`optionalDependencies`; it never downloads an executable from an arbitrary URL and never
runs a postinstall script.

Release checklist:

1. Build the engine with PyInstaller `--onedir` (or an equivalent, reproducible build).
2. Copy the executable to `bin/morphd-engine` (`.exe` on Windows) and the Web UI to the
   engine's resource directory.
3. Write `SHA256SUMS`, sign the binary with the platform certificate, and verify the
   signature.
4. `npm pack` the platform package with provenance enabled; publish with 2FA.
5. Update the `optionalDependencies` versions in `@morphd/cli`, `@morphd/tray`, and the
   desktop package in one release.
