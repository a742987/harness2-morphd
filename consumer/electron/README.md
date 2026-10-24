# @harness2-morphd/desktop

The Windows 10/11 x64 desktop shell for harness2-morphd. It is **not** a second engine.

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
permissions, and no key access in the renderer.

## Build

```powershell
npm install
npm run build
npm start -- --host dsh
npm run dist -- --win --x64
```

For development, set `MORPHD_ENGINE_BIN` to a built engine or install the platform package.
The release pipeline copies `dist/morphd-engine.exe` together with its `SHA256SUMS` into
`resources/resources/engine/`, and the packaged shell verifies the checksum before launching
it. The current release pipeline produces **unsigned** NSIS and portable installers;
Authenticode signing, SmartScreen reputation and signed updates remain pending.
