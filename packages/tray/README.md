# @morphd/tray

Optional runtime tray helper for the NPM/browser path. It attaches to a running morphd
host server, opens the UI, exposes pause/incognito/status actions, and exits when the host
server disappears. It does not start a host, a daemon, a scheduled task, or a browser.

If the desktop environment has no tray (or the optional Python `pystray`/`Pillow` extras
are not installed), it degrades to the browser UI with an explicit message. Disabling the
helper never disables the Web UI.
