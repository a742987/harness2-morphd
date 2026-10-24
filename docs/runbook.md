# Runbook — Windows 10/11 x64

This runbook covers the consumer product and the retained advanced engine path. The product
supports **Windows 10/11 x64 only**. There is no scheduled harness2-morphd process: no Task
Scheduler entry, daemon, cron, or cloud queue.

## 1. Install

### Electron desktop (recommended for end users)

- Install `harness2-morphd Setup <version>.exe` (per-user NSIS) or run the portable
  `harness2-morphd <version>.exe`. No administrator rights are required.
- Installers and the engine are **unsigned unless the release was built with Authenticode
  certificate secrets**; unsigned builds may show a SmartScreen warning.
- The desktop shell loads the local UI from the host server. The tray appears only while the
  host is running; closing the window hides to tray by default.
- `--config-only` has no tray and quits when the window closes.

### NPM launcher

```powershell
npx @harness2-morphd/cli@latest setup
npx @harness2-morphd/cli@latest doctor
```

- Node `>=20`; the launcher does not use `sudo`, `curl | sh`, or arbitrary postinstall scripts.
- The engine comes from the SHA-256-verified `@harness2-morphd/win32-x64` package or an explicit
  `MORPHD_ENGINE_BIN`. Python source is development-only (`MORPHD_ALLOW_PYTHON_ENGINE=1`).
  Missing platform packages produce an actionable error and are never downloaded from an
  arbitrary URL.
- `setup` adapts the host and opens the three-step guide. Finishing the guide closes the
  temporary installer UI; it is not resident. If no host is found, the UI still opens so you
  can select a folder manually.

### Source / development

```powershell
py -3.12 -m venv .venv-win
.venv-win\Scripts\Activate.ps1
python -m pip install -e ".[dev,tray,build]"
npm --prefix consumer/web install
npm --prefix consumer/web run build
morphd setup --host dsh --project C:\path\to\project
```

### Release build and verification

```powershell
npm run release                 # verify + engine, npm tarballs, installers, SBOM, checksums
npm run verify:release          # local unsigned build (signing status is explicit)
npm run verify:release:signed   # require Authenticode verification
```

- The GitHub workflow only releases tags equal to `v<package.json version>` and checks every
  package/Python version first.
- Set `MORPHD_SIGN_CERT_BASE64` and `MORPHD_SIGN_PASSWORD` repository secrets to enable
  Authenticode signing. Without them the release is explicitly unsigned.
- GitHub flattens release asset paths, so the published `SHA256SUMS-windows.txt` uses
  basenames. The local `release/` directory keeps relative paths for its own verification.

## 2. First configuration

1. **Host** page: confirm discovery, choose a project folder, adapt; or choose the install
   folder manually. Unsupported versions and wrong folders are never written.
2. **Models** page: add a provider, agree to its data policy, test connection, save. Use
   Ollama/LM Studio for local-only. Assign proposer/composer (same model is recommended).
3. **Learning** page: keep Advisor + Conservative unless you deliberately change it. Set the
   monthly/per-session/per-analysis budgets and retention.
4. Use the agent normally. The first analysis appears after a session with a failure,
   correction, or tool loop; smooth sessions are `noop` at $0.

The default trust/appetite is Advisor + Conservative. Auto-apply is off. It requires an
explicit confirmation, a budget lock, auto-rollback, and a snapshot.

## 3. Daily operation

- Host starts → local server and worker start; tray appears in desktop mode.
- Session ends → record + triage. Suggestions appear in the app according to focus/quiet/cap
  settings; suppressed suggestions stay in the inbox.
- Apply → snapshot + write. Undo from History within the undo window (default 7 days).
- Auto-apply (if enabled) → trial; hard regressions revert automatically.
- Host exits → worker pauses and the port closes. Next host start resumes queued jobs.

## 4. Pause, vacation, incognito

- Home or tray: pause 1 day / 1 week / until resumed, or vacation mode.
- Incognito next session: no capture, no record, no analysis, no cost. Enabling mid-session for
  an active session stops capture immediately; a tombstone prevents a late duplicate hook event
  from capturing it.
- Sensitive project / project disabled: no automatic analysis. Manual analysis shows a send
  preview first.

## 5. Budget

- Three limits: monthly, per session, per analysis; all are hard stops *before* the next call.
- Unknown prices block analysis until confirmed or estimated; harness2-morphd never treats them as $0
  or silently uses a fallback.
- The Budget page shows estimated spend and per-kind/model breakdown. The provider bill is
  authoritative.
- Changing a model does not change the executor model; the executor remains owned by the host.

## 6. Diagnostics and recovery

1. **Diagnostics** page: run local checks (no model calls), copy redacted diagnostics, inspect
   the redacted log tail.
2. **Safety mode** (Diagnostics): turns off automatic learning and auto-apply, pauses analysis,
   keeps history. Re-enable from Learning.
3. **Reset learning** (Diagnostics): deletes suggestions, diffs, jobs and trial observations;
   keeps sessions, snapshots, settings and keys. Applied harness files are not reverted.
4. **Restore original harness** (Hosts): uses the earliest pinned snapshot, removes files added
   by applied candidates where hashes still match, and detects manual edits.
5. **Disconnected UI**: the host server is gone. Reopen the host with the wrapper and launch the
   UI again. The page does not work offline or retry in the background.
6. **Host adaptation failure**: the installer rolls back; retry or choose a folder manually.
   `HOST_RUNNING` means close the host first; config changes are not hot-applied.
7. **Key error**: update the key on Models and test again. Before the fix, no analysis starts.
8. **Budget exhaustion**: raise the limit or wait for the next month. There is no “spend first,
   notify later” path.

## 7. Backup, export and deletion

- `morphd privacy export` writes a JSON bundle without keys or snapshot file contents.
- The Privacy page can export/download the same bundle and run retention immediately.
- Delete scope: session, project, or all sessions/suggestions/jobs/snapshots/logs/exports.
  Settings and keys are separate; deleting a provider removes its Credential Manager/vault entry.
- Uninstall restores host backups and removes wrappers/hooks. It never deletes host files.
  It asks whether to delete local data.
- Back up `%USERPROFILE%\.morphd` (or `MORPHD_HOME`) if you need sessions/suggestions; do not
  back up plaintext keys (there are none in the data directory).

## 8. Logs and privacy

- `%USERPROFILE%\.morphd\logs\events.jsonl` is redacted, rolls at 100 MB, and never contains
  keys or full sessions. Windows ACLs inherit from the user profile.
- Send preview uses the same redaction/exclusion path as real requests. Criteria, credentials,
  private keys, `.env` files, and excluded globs never enter the payload.
- Telemetry is off and not implemented; no device fingerprint or package list is uploaded.

## 9. Advanced engine operations (enterprise/strict)

The engine CLI remains available for task libraries, shadow evaluation, promotion and reports.
It is not used by the consumer UI.

```powershell
morphd tasks import --sessions-root "$env:USERPROFILE\.dsh\sessions" --domain backend --copy-workspace
morphd tasks split --holdout 0.2 --test 0.5
morphd run --runtime dsh --split train --budget-usd 5 --budget-calls 60 --model dsh=<model> --dry-run
morphd evaluate --runtime dsh --candidate <tree> --split test,holdout --budget-usd 5 --budget-calls 60 --model <model> --gate
morphd promote --runtime dsh --candidate <tree> --eval <report> --p2 alice --p3 bob --target C:\dsh-home
morphd rollback --runtime dsh --snapshot <id>
```

- Real engine runs default to the host-native sandbox. Container/egress is an optional strict
  mode for enterprise deployments: set `MORPHD_CONTAINER_REQUIRED=1`, run inside an external
  isolated container (for example Docker Desktop), and set `MORPHD_EGRESS_ENFORCED=1` plus the
  allowlist. The repository does not ship a container image.
- Pricing must be confirmed before real runs (`pricing.yaml`); dry-run never calls a model.
- Codex does not use bypass flags. If the pinned version lacks a usable non-bypass sandbox,
  mark Codex beta/unsupported.
- The engine CLI remains an advanced/diagnostic entry point; it is not required for normal use.

## 10. Uninstall checklist

- [ ] All host wrappers/hooks removed and backups restored.
- [ ] No harness2-morphd process, listening port, scheduled task, service, or orphan tray.
- [ ] Optional local data deleted or retained as chosen.
- [ ] Provider keys removed from Windows Credential Manager/vault.
- [ ] Host itself untouched.
