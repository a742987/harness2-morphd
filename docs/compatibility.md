# Compatibility matrix

| Component | Baseline | Notes |
|---|---|---|
| Harness² | `1cfe254e8d25` | Unmodified vendored copy or pinned install. |
| dsh | `0.2.1-rc.2` | Wrapper integration; native Cordis plugin API beta/unverified. |
| Claude Code | `2.1.302` | SessionEnd hook when writable; wrapper fallback. |
| Codex | `0.144.6` | Wrapper; native `workspace-write` sandbox + `never` approvals; no bypass. |
| Python | 3.12 / 3.13 | Engine requires 3.12+; Windows lock support included. |
| Node installer | `>=20` | dsh itself needs `^22.19.0` or `>=24.0.0`. |
| Platforms | Windows x64 C0; macOS arm64/x64 and Linux x64/arm64 C1 | Signed binaries/installers are release artifacts, not in this checkout. |

## Upgrade rules

- Upgrade one host at a time, keep the previous pin, and record the batch.
- Run three compatibility tasks; all event types must parse with no `other` bucket.
- Verify `controls_hash`/permission hash, scoring recomputation, dsh `--dump-config`
  normalization, Codex resume capability, and Claude stream event types.
- If any check fails, roll back to the pin. Unverified gaps are marked `n/a`/beta, not zero.

## Known host limitations

- dsh: no native session-close evidence; the wrapper uses a conservative turn/end + idle
  fallback. Analysis may be deferred to the next host start.
- Claude Code: hook availability depends on writable config and user consent; otherwise
  wrapper exit is the fallback.
- Codex: resume capability is probed; without it the session is single-turn and
  `convergence_turns` is `n/a`.
- Linux tray depends on AppIndicator/StatusNotifier; without it the product degrades to
  window-only with an explicit message.
