import { useRef, useState } from "react";
import { api } from "../api";
import {
  Button,
  ConfirmDialog,
  EmptyState,
  ErrorBanner,
  Field,
  InfoNote,
  Modal,
  PageHeader,
  Section,
  Select,
  Toggle,
} from "../components/UI";
import { formatBytes, formatDate, useL, useLang } from "../i18n";
import {
  MAX_IMPORT_ROWS,
  buildSettingsPatch,
  describeValue,
  diffSettings,
  extractSettingsPayload,
  type SettingDiff,
} from "../settings-import";
import { useApp } from "../state";
import type { SessionRecord, Settings } from "../types";

export function Privacy() {
  const app = useApp();
  const l = useL();
  const lang = useLang();
  const settings = app.settings;
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [previewSession, setPreviewSession] = useState<SessionRecord | null>(null);
  const [deleteScope, setDeleteScope] = useState<"session" | "project" | "all" | null>(null);
  const [deleteSession, setDeleteSession] = useState("");
  const [deleteProject, setDeleteProject] = useState("");
  const [globs, setGlobs] = useState(settings?.privacy.excluded_globs.join("\n") || "");
  const [exported, setExported] = useState<{ path: string; bundle: Record<string, unknown> } | null>(null);
  const [importRows, setImportRows] = useState<SettingDiff[]>([]);
  const [importSelected, setImportSelected] = useState<Record<string, boolean>>({});
  const [importName, setImportName] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const importInput = useRef<HTMLInputElement>(null);

  if (!settings) return null;

  const save = async (patch: Partial<typeof settings.privacy>) => {
    setError(null);
    await app.saveSettings({ privacy: { ...settings.privacy, ...patch } });
    await app.refreshAll(true);
  };

  const incognito = async (active: boolean, sessionId?: string) => {
    setBusy(true);
    try {
      await api.incognito(active, sessionId);
      await app.refreshAll(true);
      app.toast("ok", active ? "已开启隐身" : "已关闭隐身", active ? "Incognito on" : "Incognito off");
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const doPreview = async (session: SessionRecord) => {
    setBusy(true);
    setError(null);
    try {
      const payload = await api.preview(session.id);
      setPreview(payload);
      setPreviewSession(session);
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const exportData = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.exportData();
      setExported(result);
      app.toast("ok", "已生成不含 Key 的 JSON 导出", "Created a JSON export without keys");
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const download = () => {
    if (!exported) return;
    const blob = new Blob([JSON.stringify(exported.bundle, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "morphd-export.json";
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const downloadSettings = () => {
    const blob = new Blob([JSON.stringify({ settings }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "morphd-settings.json";
    anchor.click();
    URL.revokeObjectURL(url);
  };

  const chooseImport = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > 1_000_000) {
      app.toast("warn", "设置文件过大（上限 1 MB）", "Settings file is too large (1 MB limit)");
      return;
    }
    try {
      const parsed = JSON.parse(await file.text());
      const incoming = extractSettingsPayload(parsed);
      if (!incoming) {
        app.toast("warn", "这个文件里没有可识别的设置", "No recognisable settings in this file");
        return;
      }
      const rows = diffSettings(settings as unknown as Record<string, unknown>, incoming).slice(
        0,
        MAX_IMPORT_ROWS,
      );
      if (rows.length === 0) {
        app.toast("info", "没有需要导入的差异", "No differences to import");
        return;
      }
      setImportRows(rows);
      setImportSelected(Object.fromEntries(rows.map((row) => [row.path, true])));
      setImportName(file.name);
      setImportOpen(true);
    } catch {
      app.toast("warn", "无法解析这个 JSON 文件", "Could not parse this JSON file");
    }
  };

  const applyImport = async () => {
    const patch = buildSettingsPatch(importRows, importSelected);
    if (Object.keys(patch).length === 0) {
      app.toast("warn", "至少选择一项要导入的设置", "Select at least one setting to import");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = await app.saveSettings(patch as Partial<Settings>);
      if (!saved) return;
      setImportOpen(false);
      setImportRows([]);
      setImportSelected({});
      app.toast("ok", "已导入所选设置；冲突项按你的选择保留", "Selected settings imported; conflicting items kept as you chose");
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async () => {
    if (!deleteScope) return;
    if (deleteScope === "session" && !deleteSession) {
      app.toast("warn", "请选择要删除的会话", "Choose a session to delete");
      return;
    }
    if (deleteScope === "project" && !deleteProject) {
      app.toast("warn", "请选择要删除的项目", "Choose a project to delete");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await api.deleteData({
        scope: deleteScope,
        session_id: deleteScope === "session" ? deleteSession : undefined,
        project_id: deleteScope === "project" ? deleteProject : undefined,
      });
      await app.refreshData();
      setDeleteScope(null);
      app.toast("ok", "本地数据已删除，无法恢复", `Local data deleted: ${JSON.stringify(result)}`);
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const retention = async () => {
    setBusy(true);
    try {
      const result = await api.retention();
      await app.refreshData();
      app.toast("ok", `已按保留期限清理：${JSON.stringify(result)}`, `Retention applied: ${JSON.stringify(result)}`);
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const incognitoSessions = app.status?.learning.incognito_sessions || [];
  const activeSessions = app.status?.learning.active_sessions || [];

  return (
    <>
      <PageHeader
        title={l("隐私与数据", "Privacy and data")}
        subtitle={l(
          "默认本地优先：不上传遥测、不上传会话原文；发送给模型前脱敏，并允许你预览、导出、删除。",
          "Local-first by default: no telemetry or full-session upload; content is redacted before sending and can be previewed, exported, or deleted.",
        )}
      />
      {error ? <ErrorBanner error={app.handleError(error)} /> : null}

      <Section
        title={l("隐身会话", "Incognito sessions")}
        subtitle={l("隐身会话不捕获、不落盘、不分析、不产生费用。进行中开启会立即停止捕获本次临时数据。", "Incognito sessions are not captured, stored, analyzed, or billed. Enabling mid-session stops capture immediately.")}
      >
        <div className="stack">
          <InfoNote tone={app.status?.learning.incognito_next ? "warn" : "info"} icon="eye">
            {app.status?.learning.incognito_next
              ? l("下一次会话将隐身。结束前不会写入任何会话记录。", "The next session is incognito and will leave no session record.")
              : l("当前没有预设隐身；每次会话都可以单独开启。", "No incognito preset; each session can be hidden individually.")}
          </InfoNote>
          <div className="row">
            <Button
              variant={app.status?.learning.incognito_next ? "secondary" : "ghost"}
              icon="eye"
              onClick={() => void incognito(!app.status?.learning.incognito_next)}
              loading={busy}
            >
              {app.status?.learning.incognito_next ? l("取消下一次隐身", "Cancel next-session incognito") : l("下一次会话隐身", "Incognito next session")}
            </Button>
          </div>
          {incognitoSessions.length > 0 ? (
            <div className="list">
              {incognitoSessions.map((sessionId) => (
                <div className="list-item" key={sessionId}>
                  <div className="list-main">
                    <span className="list-title">{l("当前会话隐身", "Active incognito session")}</span>
                    <span className="sub mono">{sessionId}</span>
                  </div>
                  <Button size="sm" variant="ghost" onClick={() => void incognito(false, sessionId)}>
                    {l("结束隐身", "End incognito")}
                  </Button>
                </div>
              ))}
            </div>
          ) : null}
          {activeSessions.length > 0 ? (
            <div className="list">
              {activeSessions.map((session) => (
                <div className="list-item" key={session.session_id}>
                  <div className="list-main">
                    <span className="list-title">{l("正在进行的会话", "Active session")}</span>
                    <span className="sub mono">{session.host} · {session.session_id}</span>
                  </div>
                  <Button size="sm" variant="ghost" onClick={() => void incognito(true, session.session_id)}>
                    {l("立即隐身", "Incognito now")}
                  </Button>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </Section>

      <Section
        title={l("发送预览", "Send preview")}
        subtitle={l("查看发送给模型服务商的脱敏内容。第一次连接、敏感项目和含代码片段时请先确认。", "See the redacted content sent to the provider. Review on first connection, sensitive projects, and code snippets.")}
      >
        {app.sessions.length === 0 ? (
          <EmptyState icon="eye" title={l("还没有可预览的会话", "No session to preview")} />
        ) : (
          <div className="stack">
            <div className="list">
              {app.sessions.slice(0, 8).map((session) => (
                <div className="list-item" key={session.id}>
                  <div className="list-main">
                    <span className="list-title">{session.host} · {session.id}</span>
                    <span className="sub">
                      {formatDate(session.ended_at, lang)} · {session.user_turns_count} {l("轮用户消息", "user turns")}
                    </span>
                  </div>
                  <div className="list-actions">
                    <Button size="sm" variant="ghost" icon="eye" onClick={() => void doPreview(session)} loading={busy}>
                      {l("预览", "Preview")}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
            <InfoNote icon="shield">
              {l(
                "预览使用与真实请求相同的脱敏/排除逻辑；判据、凭据、私钥、.env 和排除目录不会进入发送内容。",
                "Preview uses the same redaction/exclusion logic as real requests. Criteria, credentials, private keys, .env files, and excluded folders never enter the payload.",
              )}
            </InfoNote>
          </div>
        )}
      </Section>

      <Section
        title={l("项目与数据规则", "Project and data rules")}
        subtitle={l("这些规则同时作用于会话、发送预览和建议上下文。", "These rules apply to sessions, send previews, and suggestion context.")}
      >
        <div className="stack">
          <Toggle
            checked={settings.privacy.local_only}
            onChange={(next) => void save({ local_only: next })}
            label={l("仅使用本地模型", "Local models only")}
            hint={l("禁止所有云端模型；如果未配置本地端点，分析会停止并说明原因。", "Blocks all cloud models; without a local endpoint, analysis stops with an explanation.")}
          />
          <Field
            label={l("全局排除目录 / glob（每行一个）", "Global excluded folders / globs (one per line)")}
            hint={l("项目级排除项会与此合并；排除内容不会进入任务、预览或建议上下文。", "Project exclusions are merged in; excluded content never enters tasks, previews, or proposals.")}
            htmlFor="global-globs"
          >
            <textarea
              id="global-globs"
              className="input textarea"
              value={globs}
              onChange={(event) => setGlobs(event.target.value)}
              spellCheck={false}
            />
          </Field>
          <div className="row">
            <Button
              onClick={() => void save({ excluded_globs: globs.split("\n").map((line) => line.trim()).filter(Boolean) })}
            >
              {l("保存排除规则", "Save exclusions")}
            </Button>
            <Button variant="ghost" onClick={() => setGlobs(settings.privacy.excluded_globs.join("\n"))}>
              {l("还原", "Reset")}
            </Button>
          </div>
          <div className="grid-2">
            <Field
              label={l("暂停期间的会话记录", "Recording while paused")}
              hint={l("暂停/休假时不分析；可只保留元数据、完全不记录，或保留完整摘要。", "No analysis while paused or on vacation; keep metadata only, nothing, or the full summary.")}
              htmlFor="paused-recording"
            >
              <Select
                id="paused-recording"
                value={settings.privacy.paused_session_recording}
                onChange={(event) =>
                  void save({
                    paused_session_recording: event.target.value as Settings["privacy"]["paused_session_recording"],
                  })
                }
              >
                <option value="minimal">{l("只保留最小状态（默认）", "Metadata only (default)")}</option>
                <option value="none">{l("完全不记录", "Do not record")}</option>
                <option value="full">{l("保留完整摘要", "Keep the full summary")}</option>
              </Select>
            </Field>
            <Field label={l("数据保留", "Retention")} hint={l("超期会话、任务与 workspace 会被清理；可用下方按钮立即执行。", "Expired sessions, jobs, and workspaces are removed; you can run it now.")} htmlFor="retention">
              <Select
                id="retention"
                value={settings.privacy.retention_days}
                onChange={(event) => void save({ retention_days: Number(event.target.value) })}
              >
                <option value={7}>{l("7 天", "7 days")}</option>
                <option value={30}>{l("30 天（默认）", "30 days (default)")}</option>
                <option value={90}>{l("90 天", "90 days")}</option>
                <option value={0}>{l("永久", "Forever")}</option>
              </Select>
            </Field>
            <div className="stack-sm">
              <span className="small strong">{l("立即执行保留策略", "Run retention now")}</span>
              <Button variant="ghost" icon="trash" onClick={retention} loading={busy}>
                {l("清理超期数据", "Clean expired data")}
              </Button>
            </div>
          </div>
          <Toggle
            checked={settings.privacy.telemetry}
            onChange={(next) => void save({ telemetry: next })}
            label={l("匿名遥测（默认关）", "Anonymous telemetry (off by default)")}
            hint={l("仅聚合指标与错误类型，不含会话内容、路径、代码或 Key；当前版本不会外发任何遥测。", "Aggregate metrics and error types only, no content, paths, code, or keys. This release sends no telemetry.")}
          />
          <Toggle
            checked={settings.privacy.auto_discover_hosts}
            onChange={(next) => void save({ auto_discover_hosts: next })}
            label={l("自动发现本机 Agent 工具", "Auto-discover local agent tools")}
            hint={l("关闭后只使用手动指定；发现过程始终只读元数据。", "When off, only manual selection is used; discovery is always read-only metadata.")}
          />
        </div>
      </Section>

      <Section
        title={l("导出与删除", "Export and delete")}
        subtitle={l("导出不包含明文 Key 与快照文件；删除后不可恢复。", "Exports contain no plaintext keys or snapshot files. Deletion is irreversible.")}
      >
        <div className="stack">
          <div className="row">
            <Button variant="ghost" icon="download" onClick={exportData} loading={busy}>
              {l("生成 JSON 导出", "Create JSON export")}
            </Button>
            {exported ? (
              <Button icon="download" onClick={download}>
                {l("下载导出（无 Key）", "Download export (no keys)")}
              </Button>
            ) : null}
            <Button variant="ghost" icon="download" onClick={downloadSettings}>
              {l("下载设置", "Download settings")}
            </Button>
            <Button variant="ghost" icon="upload" onClick={() => importInput.current?.click()}>
              {l("导入设置（逐项确认）", "Import settings (review each)")}
            </Button>
            <input
              ref={importInput}
              type="file"
              accept="application/json,.json"
              className="sr-only"
              aria-label={l("选择设置 JSON 文件", "Choose a settings JSON file")}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                void chooseImport(file);
              }}
            />
          </div>
          <p className="sub">
            {l(
              "导入只应用你勾选的设置项；Key、服务商、会话与项目数据不会被导入或覆盖。",
              "Import applies only the settings you check. Keys, providers, sessions, and project data are never imported or overwritten.",
            )}
          </p>
          {exported ? <p className="sub mono wrap-anywhere">{exported.path}</p> : null}
          <div className="danger-action">
            <div>
              <strong>{l("删除本地数据", "Delete local data")}</strong>
              <p className="sub">
                {l(
                  "可选单个会话、单个项目或全部会话/建议/任务/快照。设置与 Key 需单独删除。",
                  "Delete one session, one project, or all sessions/suggestions/jobs/snapshots. Settings and keys are separate.",
                )}
              </p>
            </div>
            <div className="row">
              <Button variant="danger" icon="trash" onClick={() => setDeleteScope("session")}>
                {l("删除会话", "Delete session")}
              </Button>
              <Button variant="danger" icon="trash" onClick={() => setDeleteScope("project")}>
                {l("删除项目", "Delete project")}
              </Button>
              <Button variant="danger" icon="trash" onClick={() => setDeleteScope("all")}>
                {l("删除全部数据", "Delete all data")}
              </Button>
            </div>
          </div>
          <InfoNote icon="key">
            {l(
              "删除 Key 请到模型页逐个删除；系统 Keychain 中的条目会同时移除。",
              "Delete keys on the Models page; entries in the OS keychain are removed with them.",
            )}
          </InfoNote>
        </div>
      </Section>

      {importOpen ? (
        <Modal
          title={l("导入设置（逐项确认）", "Import settings (review each)")}
          subtitle={`${importName} · ${importRows.length} ${l("项差异", "differences")}`}
          onClose={() => setImportOpen(false)}
          wide
          footer={
            <>
              <Button variant="ghost" onClick={() => setImportOpen(false)} disabled={busy}>
                {l("取消", "Cancel")}
              </Button>
              <Button onClick={() => void applyImport()} loading={busy} icon="check">
                {l("应用所选设置", "Apply selected settings")}
              </Button>
            </>
          }
        >
          <p className="sub">
            {l(
              "每一项都对照当前值；取消勾选即保留当前值。冲突项不会自动覆盖。",
              "Each item is compared with the current value. Unchecked items keep the current value; conflicts are never overwritten automatically.",
            )}
          </p>
          <div className="row">
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                setImportSelected(Object.fromEntries(importRows.map((row) => [row.path, true])))
              }
            >
              {l("全选", "Select all")}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() =>
                setImportSelected(Object.fromEntries(importRows.map((row) => [row.path, false])))
              }
            >
              {l("全不选", "Select none")}
            </Button>
          </div>
          <div className="list">
            {importRows.map((row) => (
              <label className="list-item" key={row.path}>
                <input
                  type="checkbox"
                  checked={Boolean(importSelected[row.path])}
                  onChange={(event) =>
                    setImportSelected((current) => ({
                      ...current,
                      [row.path]: event.target.checked,
                    }))
                  }
                />
                <div className="list-main">
                  <span className="list-title mono wrap-anywhere">{row.path}</span>
                  <span className="sub wrap-anywhere">
                    {l("当前", "Current")}: {describeValue(row.current)}
                  </span>
                  <span className="sub wrap-anywhere">
                    {l("导入", "Incoming")}: {describeValue(row.incoming)}
                  </span>
                </div>
              </label>
            ))}
          </div>
          <InfoNote icon="shield">
            {l(
              "导入文件仅用于设置项。Key、服务商配置、会话、候选与项目数据不会被读取或写入。",
              "Import applies to settings only. Keys, provider configuration, sessions, candidates, and project data are neither read nor written.",
            )}
          </InfoNote>
        </Modal>
      ) : null}

      {preview && previewSession ? (
        <Modal
          title={l("发送预览（脱敏）", "Send preview (redacted)")}
          subtitle={`${previewSession.host} · ${previewSession.id}`}
          onClose={() => { setPreview(null); setPreviewSession(null); }}
          wide
          footer={
            <Button variant="ghost" onClick={() => { setPreview(null); setPreviewSession(null); }}>
              {l("关闭", "Close")}
            </Button>
          }
        >
          <div className="grid-3">
            <div className="fact">
              <strong>{l("文件", "Files")}</strong>
              {Object.keys((preview.files as Record<string, unknown>) || {}).length}
            </div>
            <div className="fact">
              <strong>{l("预计 tokens", "Estimated tokens")}</strong>
              {String((preview.estimated_tokens as number) || "—")}
            </div>
            <div className="fact">
              <strong>{l("排除项", "Excluded")}</strong>
              {((preview.excluded as string[]) || []).length}
            </div>
          </div>
          <div className="stack-sm">
            <strong>{l("用户消息", "User turns")}</strong>
            <div className="list">
              {((preview.user_turns as { text: string; corrective: boolean }[]) || []).map((turn, index) => (
                <div className="list-item" key={index}>
                  <div className="list-main">
                    <span className="list-title">U{index + 1}{turn.corrective ? ` · ${l("纠正", "correction")}` : ""}</span>
                    <span className="sub">{turn.text}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="stack-sm">
            <strong>{l("将会发送的文件", "Files that would be sent")}</strong>
            <ul className="step-list">
              {Object.keys((preview.files as Record<string, unknown>) || {}).map((name) => (
                <li key={name}><span>•</span><span className="mono small">{name}</span></li>
              ))}
            </ul>
          </div>
          <div className="stack-sm">
            <strong>{l("脱敏", "Redactions")}</strong>
            <p className="sub">
              {l("文本标签", "Text labels")}:{" "}
              {JSON.stringify((preview.redactions as { text?: string[] })?.text || [])}
            </p>
            <p className="sub">
              {l("文件级脱敏", "File redactions")}:{" "}
              {JSON.stringify((preview.redactions as { files?: unknown[] })?.files || [])}
            </p>
          </div>
          {preview.truncated ? <InfoNote tone="warn" icon="alert">{l("预览已截断；实际请求也会显式标注截断。", "The preview was truncated and the actual request is marked as truncated too.")}</InfoNote> : null}
        </Modal>
      ) : null}

      {deleteScope ? (
        <ConfirmDialog
          title={l("确认删除本地数据？", "Delete local data?")}
          confirmLabel={l("永久删除", "Delete permanently")}
          onConfirm={doDelete}
          onClose={() => setDeleteScope(null)}
          danger
          busy={busy}
        >
          {deleteScope === "session" ? (
            <Field label={l("选择会话", "Choose a session")} htmlFor="delete-session">
              <Select id="delete-session" value={deleteSession} onChange={(event) => setDeleteSession(event.target.value)}>
                <option value="">{l("请选择", "Choose")}</option>
                {app.sessions.map((session) => (
                  <option key={session.id} value={session.id}>{session.id} · {session.host}</option>
                ))}
              </Select>
            </Field>
          ) : null}
          {deleteScope === "project" ? (
            <Field label={l("选择项目", "Choose a project")} htmlFor="delete-project">
              <Select id="delete-project" value={deleteProject} onChange={(event) => setDeleteProject(event.target.value)}>
                <option value="">{l("请选择", "Choose")}</option>
                {app.projects.map((project) => (
                  <option key={project.id} value={project.id}>{project.domain} · {project.id}</option>
                ))}
              </Select>
            </Field>
          ) : null}
          <p>
            {deleteScope === "all"
              ? l("将删除全部会话、建议、任务、快照、日志与导出；设置与 Key 保留。", "Deletes all sessions, suggestions, jobs, snapshots, logs, and exports. Settings and keys remain.")
              : l("删除后不可恢复。已应用的 Agent 工作方式改动不会自动回退。", "Deletion is irreversible. Applied agent changes are not automatically reverted.")}
          </p>
          <p className="sub">{l("磁盘占用", "Disk usage")}: {formatBytes(app.diagnostics?.disk.bytes || 0, lang)}</p>
        </ConfirmDialog>
      ) : null}
    </>
  );
}
