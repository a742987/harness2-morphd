# @harness2-morphd/cli

The one-command launcher for harness2-morphd on **Windows 10/11 x64**. It is intentionally
thin:

- no `postinstall`;
- no `curl | sh`;
- no arbitrary binary download;
- no elevation prompt;
- no API keys in npm config, environment, logs, or cache.

The engine comes from `@harness2-morphd/win32-x64` (SHA-256 verified) or an explicit
`MORPHD_ENGINE_BIN`. Python source execution is development-only and requires
`MORPHD_ALLOW_PYTHON_ENGINE=1`.

```powershell
npx @harness2-morphd/cli@latest setup
npx @harness2-morphd/cli@latest doctor
npx @harness2-morphd/cli@latest update
npx @harness2-morphd/cli@latest uninstall
```

`setup` adapts a detected Harness and opens the temporary local UI for the three-step
guide. Finishing the guide closes the temporary installer UI; it never stays resident. If no
host is found, the UI still opens so you can choose a folder manually.

`npx` leaves no global package. `npm i -g @harness2-morphd/cli` is optional and can be upgraded
or removed normally.

The package is intended to be published with npm provenance and 2FA. The platform package is
selected via `optionalDependencies`; a missing package produces an actionable error rather
than a fallback download. Current release artifacts are **unsigned**.
