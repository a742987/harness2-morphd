# @harness2-morphd/tray

Optional Windows 10/11 x64 tray helper for the NPM/browser path. It attaches to a running
harness2-morphd host server, opens the UI, exposes pause/incognito/status actions, and exits
when the host server disappears. It does not start a host, a daemon, a scheduled task, or a
browser.

If the desktop session has no notification area (or the optional Python `pystray`/`Pillow`
extras are not installed), it degrades to the browser UI with an explicit message. Disabling
the helper never disables the Web UI.
