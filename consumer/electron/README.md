# @harness2-morphd/desktop

The desktop shell for Windows (C0), macOS and Linux (C1). It is **not** a second engine.

## Lifecycle

- Start your host through the adapted wrapper (interactive CLI hosts need a terminal).
- Double-click/open the desktop app: it attaches to the running local server and shows the
  same Web UI. Tray appears only while the host server is alive.
- `--launch-host` asks the shell to start the adapted wrapper itself. Use this only for hosts
  that do not need an interactive TTY.
- `--config-only` opens a temporary configuration UI; no tray, no automatic learning, and it
  exits when the window closes.
- Closing the window hides to tray by default (configurable to exit the UI). It never closes
  the host. “Stop learning and exit” asks for confirmation, writes the runtime stop marker,
  and leaves the host running.
- Host exit → server disappears → shell quits and the tray is destroyed. No auto-start or
  background service is registered.

## Security

`contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, whitelisted preload,
fixed/validated IPC channels, strict CSP from the local server, no remote navigation, denied
permissions, no key access in the renderer, and signed-update-only policy (release CI).

## Build

```sh
npm install
npm run build
npm start -- --host dsh
npm run dist          # electron-builder; requires a signed engine in resources/engine
```

Place the signed engine at `resources/engine` (or set `MORPHD_ENGINE_BIN`). The release
pipeline must Authenticode-sign Windows artifacts, notarize macOS, and verify SHA256/SBOM.
