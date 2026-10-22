import { useState } from "react";
import { api } from "../api";
import {
  Badge,
  Button,
  ConfirmDialog,
  CopyButton,
  EmptyState,
  ErrorBanner,
  Field,
  InfoNote,
  Input,
  Modal,
  PageHeader,
  Section,
  StatusMark,
} from "../components/UI";
import { formatDate, useL, useLang } from "../i18n";
import { useApp } from "../state";
import type { HostCandidate } from "../types";

const STATUS_COPY: Record<string, { zh: string; en: string }> = {
  adapt: { zh: "可适配", en: "Ready" },
  adaptable: { zh: "可适配", en: "Ready" },
  installed: { zh: "已安装", en: "Installed" },
  version_unsupported: { zh: "版本不支持", en: "Unsupported version" },
  permission_denied: { zh: "权限不足", en: "Permission denied" },
  config_missing: { zh: "缺少配置目录", en: "Config missing" },
  identity_unknown: { zh: "身份不确定", en: "Identity uncertain" },
};

export function Hosts() {
  const app = useApp();
  const l = useL();
  const lang = useLang();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<HostCandidate | null>(null);
  const [projectRoot, setProjectRoot] = useState("");
  const [manualOpen, setManualOpen] = useState(false);
  const [manualKind, setManualKind] = useState<"dsh" | "claude-code" | "codex">("dsh");
  const [installPath, setInstallPath] = useState("");
  const [configPath, setConfigPath] = useState("");
  const [uninstall, setUninstall] = useState<string | null>(null);
  const [restore, setRestore] = useState<string | null>(null);

  const candidates = app.hosts.filter((host) => !host.installed);
  const installed = app.status?.hosts || [];

  const run = async (fn: () => Promise<unknown>, successZh: string, successEn: string) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await app.refreshData();
      app.toast("ok", successZh, successEn);
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const discover = () => run(async () => {
    await api.discoverHosts();
  }, "已重新检查本机 Harness", "Re-scanned local Harnesses");

  const adapt = (candidate: HostCandidate) => run(async () => {
    await api.adaptHost({ candidate, project_root: projectRoot || undefined });
    setSelected(null);
  }, "宿主已适配，重启宿主后生效", "Host adapted; restart the host to activate");

  const adaptManual = () => run(async () => {
    if (!installPath.trim()) {
      throw new Error(l("请填写安装目录或可执行文件", "Enter an install folder or executable"));
    }
    await api.adaptHost({
      manual: true,
      kind: manualKind,
      install_path: installPath,
      config_path: configPath || undefined,
      project_root: projectRoot || undefined,
    });
    setManualOpen(false);
  }, "宿主已适配", "Host adapted");

  const chooseFolder = async (kind: "install" | "config" | "project") => {
    try {
      const result = await api.pickFolder(kind);
      if (kind === "install") setInstallPath(result.path);
      else if (kind === "config") setConfigPath(result.path);
      else setProjectRoot(result.path);
    } catch (caught) {
      const apiError = app.handleError(caught);
      if (apiError) {
        app.toast(
          "warn",
          l("当前环境无法打开系统选择器；直接输入路径即可。", "This environment cannot open a native picker; type the path instead."),
          "Native folder picker unavailable; type the path.",
        );
      }
    }
  };

  return (
    <>
      <PageHeader
        title={l("宿主发现与适配", "Hosts")}
        subtitle={l(
          "只检查 PATH、npm 全局目录和常见安装/配置目录；不整盘扫描、不读取凭据、不读取会话内容。",
          "Checks PATH, npm global locations, and common install/config folders. No full-disk scan, no reading credentials or session content.",
        )}
        actions={
          <>
            <Button variant="ghost" icon="refresh" onClick={discover} loading={busy}>
              {l("重新发现", "Scan again")}
            </Button>
            <Button variant="ghost" icon="pin" onClick={() => setManualOpen(true)}>
              {l("手动指定文件夹", "Choose a folder")}
            </Button>
          </>
        }
      />
      {error ? <ErrorBanner error={app.handleError(error)} onRetry={discover} /> : null}

      <Section
        title={l("已适配的宿主", "Adapted hosts")}
        subtitle={l(
          "UI 与引擎随宿主启停；关闭宿主后不会留下 morphd 进程、端口、计划任务或托盘。",
          "The UI and engine follow the host lifecycle. When the host exits, no morphd process, port, scheduled task, or tray remains.",
        )}
      >
        {installed.length === 0 ? (
          <EmptyState icon="host" title={l("还没有适配任何宿主", "No host adapted yet")}>
            {l(
              "可以自动发现 dsh、Claude Code、Codex；找不到时手动选择安装目录。",
              "morphd can discover dsh, Claude Code, and Codex. If not found, choose the install folder manually.",
            )}
          </EmptyState>
        ) : (
          <div className="stack">
            {installed.map((host) => (
              <div className="host-card" key={host.host}>
                <div className="host-head">
                  <div className="provider-title">
                    <span className="host-mark" aria-hidden="true">{host.label.slice(0, 1)}</span>
                    <div>
                      <div className="provider-name">{host.label}</div>
                      <div className="provider-id">{host.version} · {host.integration}</div>
                    </div>
                  </div>
                  <div className="row">
                    <Badge variant={host.status === "adapted" ? "accent" : "dashed"}>
                      {host.status === "adapted" ? l("已适配", "Adapted") : l("beta / wrapper", "beta / wrapper")}
                    </Badge>
                    <StatusMark tone={host.connected ? "ok" : "idle"}>
                      {host.connected ? l("已连接", "Connected") : l("未连接", "Disconnected")}
                    </StatusMark>
                  </div>
                </div>
                <div className="grid-2">
                  <div className="stack-sm">
                    <span className="small strong">{l("启动 wrapper", "Launch wrapper")}</span>
                    {host.wrapper ? (
                      <>
                        <span className="mono small wrap-anywhere">{host.wrapper}</span>
                        <CopyButton text={host.wrapper} label={l("复制路径", "Copy path")} />
                      </>
                    ) : (
                      <span className="sub">{l("原生 hook 模式，无需 wrapper。", "Native hook mode; no wrapper needed.")}</span>
                    )}
                  </div>
                  <div className="stack-sm">
                    <span className="small strong">{l("项目范围", "Project scope")}</span>
                    <span className="small wrap-anywhere">{host.project_root || l("使用宿主配置目录", "Host config directory")}</span>
                    <span className="small">
                      {l("通过 wrapper 启动宿主时 UI 会随宿主出现；宿主退出后分析暂停，下次启动续跑。", "The UI appears with the host when launched through the wrapper. Analysis pauses on host exit and resumes next launch.")}
                    </span>
                  </div>
                </div>
                <div className="row">
                  <Button size="sm" variant="ghost" icon="undo" onClick={() => setRestore(host.host)}>
                    {l("恢复原始 harness", "Restore original harness")}
                  </Button>
                  <Button size="sm" variant="ghost" icon="trash" onClick={() => setUninstall(host.host)}>
                    {l("移除适配", "Remove integration")}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section
        title={l("发现的 Harness", "Discovered Harnesses")}
        subtitle={l("发现后先由你确认；不会静默修改宿主配置。", "Nothing is modified until you confirm.")}
        actions={
          <Button size="sm" variant="ghost" icon="refresh" onClick={discover} loading={busy}>
            {l("刷新", "Refresh")}
          </Button>
        }
      >
        {candidates.length === 0 ? (
          <EmptyState icon="terminal" title={l("没有发现可适配的宿主", "No adaptable host found")}>
            {l(
              "确认宿主已安装并在 PATH 中；或使用“手动指定文件夹”。morphd 不会自动安装或升级宿主。",
              "Make sure the host is installed and on PATH, or choose a folder manually. morphd never installs or upgrades the host.",
            )}
          </EmptyState>
        ) : (
          <div className="list">
            {candidates.map((host) => (
              <div className="list-item" key={host.id}>
                <div className="list-main">
                  <div className="row">
                    <span className="list-title">{host.label} {host.version}</span>
                    <Badge variant={host.compatible ? "accent" : "dashed"}>
                      {STATUS_COPY[host.status]?.[lang] || host.status}
                    </Badge>
                    <Badge>{host.integration}</Badge>
                  </div>
                  <span className="sub mono wrap-anywhere">{host.executable}</span>
                  <span className="sub wrap-anywhere">
                    {l("配置", "Config")}: {host.config_dir || "—"}
                  </span>
                  {host.reason ? <span className="sub">{host.reason}</span> : null}
                </div>
                <div className="list-actions">
                  <Button
                    size="sm"
                    icon="check"
                    onClick={() => {
                      setSelected(host);
                      if (host.project_root) setProjectRoot(host.project_root);
                    }}
                    disabled={!host.compatible}
                  >
                    {l("适配", "Adapt")}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section
        title={l("兼容矩阵", "Compatibility matrix")}
        subtitle={l("版本不在矩阵内时零写入；不支持的宿主只展示，不强行适配。", "Unsupported versions are never written to; unsupported hosts are only shown.")}
      >
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{l("宿主", "Host")}</th>
                <th>{l("基线版本", "Baseline")}</th>
                <th>{l("v0.5 能力", "v0.5 capability")}</th>
                <th>{l("会话结束信号", "Session end")}</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>dsh</td>
                <td className="mono">0.2.1-rc.2</td>
                <td>{l("完整（工具/技能/脚本）", "Full (instructions/skills/scripts)")}</td>
                <td>{l("原生插件可用时；否则 wrapper", "Native plugin when available; otherwise wrapper")}</td>
              </tr>
              <tr>
                <td>Claude Code</td>
                <td className="mono">2.1.302</td>
                <td>{l("beta；SessionEnd hook 或 wrapper", "Beta; SessionEnd hook or wrapper")}</td>
                <td>{l("SessionEnd；否则进程退出", "SessionEnd; otherwise process exit")}</td>
              </tr>
              <tr>
                <td>Codex</td>
                <td className="mono">0.144.6</td>
                <td>{l("beta；wrapper，原生 sandbox", "Beta; wrapper, native sandbox")}</td>
                <td>{l("进程退出/会话结束", "Process exit / session end")}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <InfoNote tone="warn" icon="terminal">
          {l(
            "当前实现通过 wrapper/hook 绑定宿主生命周期；原生插件 API 仍需真机证据。没有 hook 时，会话结束分析会在下次宿主启动时从队列续跑，不会在后台常驻。",
            "This implementation binds lifecycle through wrappers/hooks; native plugin APIs still need real-host evidence. Without a hook, analysis resumes from the queue on the next host launch and never runs in the background.",
          )}
        </InfoNote>
      </Section>

      {selected ? (
        <Modal
          title={l(`适配 ${selected.label}`, `Adapt ${selected.label}`)}
          subtitle={`${selected.version} · ${selected.executable}`}
          onClose={() => setSelected(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setSelected(null)} disabled={busy}>
                {l("取消", "Cancel")}
              </Button>
              <Button onClick={() => adapt(selected)} loading={busy} icon="check">
                {l("确认适配", "Adapt")}
              </Button>
            </>
          }
        >
          <p>
            {l(
              "morphd 会先备份将被修改的宿主配置，写入 wrapper/hook，然后执行无模型自检。任何一步失败都会回滚。",
              "morphd backs up any host config it modifies, writes the wrapper/hook, then runs a zero-model self-check. Any failure rolls back.",
            )}
          </p>
          <Field
            label={l("项目文件夹（推荐）", "Project folder (recommended)")}
            hint={l("建议写入这个项目的说明书/技能/脚本，而不是全局配置。", "Suggestions apply to this project's instructions/skills/scripts rather than global config.")}
            htmlFor="host-project"
          >
            <div className="input-row">
              <Input
                id="host-project"
                value={projectRoot}
                onChange={(event) => setProjectRoot(event.target.value)}
                placeholder="/path/to/project"
                spellCheck={false}
              />
              <Button size="sm" variant="ghost" onClick={() => void chooseFolder("project")}>
                {l("选择", "Browse")}
              </Button>
            </div>
          </Field>
          <InfoNote icon="shield">
            {l(
              "不修改宿主权限，不读取凭据，不安装或升级宿主。适配可卸载并恢复备份。",
              "No permission changes, no credential reads, no host installation or upgrade. Adaptation can be uninstalled and backups restored.",
            )}
          </InfoNote>
        </Modal>
      ) : null}

      {manualOpen ? (
        <Modal
          title={l("手动指定 Harness", "Choose a Harness manually")}
          onClose={() => setManualOpen(false)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setManualOpen(false)} disabled={busy}>
                {l("取消", "Cancel")}
              </Button>
              <Button onClick={adaptManual} loading={busy} icon="check">
                {l("校验并适配", "Validate and adapt")}
              </Button>
            </>
          }
        >
          <p className="sub">
            {l(
              "错误目录不会写入任何文件。安装目录需包含 dsh/claude/codex 可执行文件；配置目录需通过身份校验。",
              "A wrong folder is never written to. The install folder must contain dsh/claude/codex; the config folder must pass identity checks.",
            )}
          </p>
          <Field label={l("宿主", "Host")} htmlFor="manual-kind">
            <select
              id="manual-kind"
              className="input select"
              value={manualKind}
              onChange={(event) => setManualKind(event.target.value as typeof manualKind)}
            >
              <option value="dsh">dsh</option>
              <option value="claude-code">Claude Code</option>
              <option value="codex">Codex</option>
            </select>
          </Field>
          <Field label={l("安装目录或可执行文件", "Install folder or executable")} htmlFor="manual-install">
            <div className="input-row">
              <Input
                id="manual-install"
                value={installPath}
                onChange={(event) => setInstallPath(event.target.value)}
                spellCheck={false}
              />
              <Button size="sm" variant="ghost" onClick={() => void chooseFolder("install")}>
                {l("选择", "Browse")}
              </Button>
            </div>
          </Field>
          <Field label={l("配置目录（可选）", "Config folder (optional)")} htmlFor="manual-config">
            <div className="input-row">
              <Input
                id="manual-config"
                value={configPath}
                onChange={(event) => setConfigPath(event.target.value)}
                spellCheck={false}
              />
              <Button size="sm" variant="ghost" onClick={() => void chooseFolder("config")}>
                {l("选择", "Browse")}
              </Button>
            </div>
          </Field>
          <Field label={l("项目文件夹（可选）", "Project folder (optional)")} htmlFor="manual-project">
            <Input
              id="manual-project"
              value={projectRoot}
              onChange={(event) => setProjectRoot(event.target.value)}
              spellCheck={false}
            />
          </Field>
          <InfoNote tone="warn" icon="alert">
            {l(
              "只选择项目文件夹不等于适配成功；morphd 会明确标注“未连接宿主”，不会承诺自动触发。",
              "Choosing only a project folder is not adaptation; morphd will clearly show “host not connected” and will not promise automatic triggers.",
            )}
          </InfoNote>
        </Modal>
      ) : null}

      {uninstall ? (
        <ConfirmDialog
          title={l("移除宿主适配？", "Remove host integration?")}
          confirmLabel={l("移除并恢复备份", "Remove and restore backups")}
          onConfirm={() => run(async () => {
            await api.uninstallHost(uninstall);
            setUninstall(null);
          }, "已移除适配并恢复备份", "Integration removed and backups restored")}
          onClose={() => setUninstall(null)}
          danger
          busy={busy}
        >
          <p>
            {l(
              "会恢复适配时备份的宿主配置，移除 wrapper/hook 与安装清单；不会删除你的会话数据或宿主自身文件。",
              "Host config backups are restored, and the wrapper/hook and install record are removed. Your session data and host files are not deleted.",
            )}
          </p>
          <p className="sub">
            {l("之后可在隐私页选择是否删除本地 morphd 数据。", "You can delete local morphd data separately on the Privacy page.")}
          </p>
        </ConfirmDialog>
      ) : null}

      {restore ? (
        <ConfirmDialog
          title={l("恢复原始 harness？", "Restore the original harness?")}
          confirmLabel={l("恢复并标记已撤销", "Restore and mark reverted")}
          onConfirm={() => run(async () => {
            await api.restoreOriginal(restore, projectRoot || undefined);
            setRestore(null);
          }, "已恢复到最初的 harness 快照", "Restored the earliest harness snapshot")}
          onClose={() => setRestore(null)}
          danger
          busy={busy}
        >
          <p>
            {l(
              "会恢复这个项目最早的快照，并移除 morphd 应用过的候选文件。不会删除你的 Key、设置或证据历史。",
              "Restores this project's earliest snapshot and removes files added by applied suggestions. Keys, settings, and evidence history are kept.",
            )}
          </p>
        </ConfirmDialog>
      ) : null}
      <p className="sub">
        {l("本地服务启动", "Local service started")}: {formatDate(app.status?.started_at, lang)}
      </p>
    </>
  );
}
