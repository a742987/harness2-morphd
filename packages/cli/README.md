# @harness2-morphd/cli

The one-command launcher for harness2-morphd. It is intentionally thin:

- no `postinstall`;
- no `curl | sh`;
- no arbitrary binary download;
- no sudo;
- no API keys in npm config, environment, logs, or cache.

The engine comes from a signed platform package (`@harness2-morphd/<os>-<arch>`), an explicit
`MORPHD_ENGINE_BIN`, or, for development only, `python3 -m morphd`.

```sh
npx @harness2-morphd/cli@latest setup
npx @harness2-morphd/cli@latest doctor
npx @harness2-morphd/cli@latest update
npx @harness2-morphd/cli@latest uninstall
```

`setup` adapts a detected Harness and opens the temporary local UI for the three-step
guide. Press Ctrl+C after finishing the guide; the installer never stays resident.

`npx` leaves no global package. `npm i -g @harness2-morphd/cli` is optional and can be upgraded or
removed normally.

The package is published with npm provenance and 2FA. Platform packages are selected via
`optionalDependencies`; a missing platform package produces an actionable error rather
than a fallback download.
