Place the one-file Windows engine at resources/engine/morphd-engine.exe before packaging.
scripts/build_release.ps1 copies packages/platform/win32-x64/bin/morphd-engine.exe here and
verifies SHA256SUMS. The application never downloads an engine at runtime.

The current release is unsigned; Authenticode signing remains pending.
