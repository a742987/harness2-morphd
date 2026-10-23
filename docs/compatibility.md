# Compatibility matrix

harness2-morphd supports **Windows 10/11 x64 only**. macOS, Linux, Windows ARM64 and WSL-hosted
product runs are not supported.

| Component | Baseline | Notes |
|---|---|---|
| Harness² | `1cfe254e8d25` | Unmodified vendored copy or pinned install. |
| dsh | `0.2.1-rc.2` | Wrapper integration; native Cordis plugin API beta/unverified. |
| Claude Code | `2.1.302` | SessionEnd hook when writable; wrapper fallback. |
| Codex | `0.144.6` | Wrapper; native `workspace-write` sandbox + `never` approvals; no bypass. |
| Python | 3.12 / 3.13 | Source development and engine build only; end users need no Python. |
| Node installer | `>=20` | dsh itself needs `^22.19.0` or `>=24.0.0`. |
| Platform | Windows 10/11 x64 | Electron NSIS/portable and `@harness2-morphd/win32-x64`. |
| Signing | None in the current release | Authenticode/SmartScreen validation remains pending. |

## Upgrade rules

- Upgrade one host at a time, keep the previous pin, and record the batch.
- Run three compatibility tasks; all event types must parse with no `other` bucket.
- Verify `controls_hash`/permission hash, scoring recomputation, dsh `--dump-config`
  normalization, Codex resume capability, and Claude stream event types.
- If any check fails, roll back to the pin. Unverified gaps are marked `n/a`/beta, not zero.
- Re-run the Windows test suite and rebuild the engine after changing the host pins.

## Known host limitations

- dsh: no native session-close evidence; the wrapper uses a conservative turn/end + idle
  fallback. Analysis may be deferred to the next host start.
- Claude Code: hook availability depends on writable config and user consent; otherwise
  wrapper exit is the fallback.
- Codex: resume capability is probed; without it the session is single-turn and
  `convergence_turns` is `n/a`.
- Tray: Windows only; browser-only installs and systems without a desktop session degrade to
  the browser UI with an explicit message.
