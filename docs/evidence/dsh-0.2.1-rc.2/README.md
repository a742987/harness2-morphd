# dsh 0.2.1-rc.2 evidence

Pinned source: `deepseek-ai/deepseek-harness` tag `dsh-v0.2.1-rc.2`, commit `687f282e8625`.

## Contract facts used by harness2-morphd

- Headless: one task per invocation; `--json` event stream; opening `session` event carries
  `sessionId`; `--session-id` adopts a persisted session; exit 0 only for completed turns.
- Session logs: `$DSH_HOME/sessions/--<normalized-cwd>--/<id>/session.vN.jsonl.zstd`, physical
  header first, zstd-compressed. The reader tolerates vN and uncompressed logs.
- Profiles: `$DSH_HOME/profiles/<id>/package.json` with `dsh.profile.bundles`, and
  `cordis.patch.yml`; a profile without the headless bundle has no runner.
- Approval: `ask` delegates to composed answerers; no answerer fails closed; `never` rejects
  every ask. harness2-morphd mounts a controlled answerer only in offline profiles.
- Credentials: managed `$DSH_HOME/.credentials.yaml` outranks env, cwd `.env`, home `.env`.
  harness2-morphd never writes that file into a variant home and passes credentials via env.
- Sandbox: file effects only. harness2-morphd fixes `workspace-write` + `ask` and removes the
  `danger-full-access` preset from generated profiles.

## Live checks (M0)

```powershell
dsh --version
$env:DSH_HOME = "C:\dsh-home"
dsh --profile md-preflight --dump-config > dsh-dump.txt
dsh --profile <id> --json "echo probe" > dsh-probe.jsonl
```

Store sanitized outputs here. The adapter's preflight already performs a profile dump against a
temporary home; the live probe additionally verifies model credentials and the launcher/exit
mapping.
