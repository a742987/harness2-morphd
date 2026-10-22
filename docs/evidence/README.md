# Evidence index

| runtime | pinned | captured here | still required before judging |
|---|---|---|---|
| dsh | `0.2.1-rc.2` / `687f282e8625` | source contract summary | live `--version`, profile dump, one headless probe |
| Codex | `0.144.6` | upstream substrate contract | `--version`, `exec --help`, `exec resume --help`, one probe |
| Claude Code | `2.1.302` | real `--version` and `--help` | stream-json/usage/result/resume samples from a real call |

No real model call was made while assembling this repository. Items marked "still required"
are why the corresponding metrics must be reported as `n/a` until M0 captures them.
