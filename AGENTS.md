# Agent rules for harness2-morphd

## Never launch real hosts, GUI apps, or the user's applications

This product targets Windows 10/11 x64, but agent sessions often run under WSL.
An agent once called `ConsumerService.hosts_discover(force=True)` with the
inherited real `PATH`. On this machine `codex --version` resolves to
`H:\Codex++\codex-plus-plus-manager.exe`, which opens a GUI instead of printing
a version. Repeated probes repeatedly opened the user's Codex++ window.

Hard rules for every agent and every test:

1. Use only the fake hosts in `tests/fakes/` (`dsh`, `claude`, `codex`) and an
   isolated temp `PATH`, `MORPHD_HOME`, `DSH_HOME`, `CLAUDE_CONFIG_DIR`, and
   `CODEX_HOME`.
2. Never run host discovery or validation against the real machine. This
   includes `HostManager.discover()`, `inspect_executable()`,
   `hosts_discover(force=True)`, and any `--version` probe of a real
   `dsh`/`claude`/`codex`/Codex++ binary.
3. Host-discovery tests must clear `PATH`, stub `winreg`, and use temp
   directories. A test that needs a real binary must be explicitly marked and
   run only with the user's approval.
4. Never start a GUI process or windowed application from an agent command.
   If one is started accidentally, terminate only the process tree that was
   started and report it immediately.
5. No background agent may read or modify the real user environment, host
   configuration, credentials, or sessions without explicit user approval.

Windows Python tests must run through the Windows venv, for example:

```text
./.venv-win/Scripts/python.exe -X utf8 -m pytest -q <paths>
```
