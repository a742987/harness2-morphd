import { useState } from "react";
import { api } from "../api";
import {
  Badge,
  Button,
  ConfirmDialog,
  CopyButton,
  ErrorBanner,
  InfoNote,
  KeyValue,
  PageHeader,
  Section,
  StatusMark,
} from "../components/UI";
import { formatBytes, formatDate, useL, useLang } from "../i18n";
import { useApp } from "../state";

export function Diagnostics() {
  const app = useApp();
  const l = useL();
  const lang = useLang();
  const diag = app.diagnostics;
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [safety, setSafety] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);

  const checkCopy = (check: { id?: string; name: string; detail: string }): { name: string; detail: string } => {
    if (check.id === "permissions") {
      return {
        name: l("存储权限", "Storage permissions"),
        detail: l("当前用户可读写；数据目录 0700", "Current user can read/write; data directory 0700"),
      };
    }
    if (check.id === "telemetry") {
      return {
        name: l("遥测", "Telemetry"),
        detail: l("默认关闭，当前版本不外发", "Off by default; this release sends nothing"),
      };
    }
    if (check.id?.startsWith("host:")) {
      const host = check.id.slice(5);
      return { name: l(`${host} 适配`, `${host} adaptation`), detail: check.detail };
    }
    if (check.id?.startsWith("provider:")) {
      const provider = (diag?.providers || []).find((item) => item.id === check.id?.slice(9));
      return {
        name: provider ? l(`模型 ${provider.label}`, `Model ${provider.label}`) : check.name,
        detail: check.detail,
      };
    }
    return { name: check.name, detail: check.detail };
  };

  const sanitized = () => {
    if (!diag) return "";
    return JSON.stringify(
      {
        app_version: diag.app_version,
        permissions_ok: diag.permissions_ok,
        disk: diag.disk,
        checks: diag.checks,
        hosts: diag.hosts,
        providers: diag.providers,
        logs_tail: diag.logs_tail.slice(-20),
        budget: diag.budget,
      },
      null,
      2,
    );
  };

  const enableSafety = async () => {
    setBusy(true);
    setError(null);
    try {
      const saved = await app.saveSettings({ enabled: false, auto_apply: false });
      if (saved) {
        await app.refreshAll(true);
        setSafety(false);
        app.toast(
          "ok",
          "安全模式已开启：不再自动分析或自动应用；历史仍可查看",
          "Safety mode on: no automatic analysis or apply; history remains",
        );
      }
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const resetLearning = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.resetLearning();
      await app.refreshData();
      setResetOpen(false);
      app.toast("ok", "学习记录已重置；Key 与设置保留", "Learning data reset; keys and settings kept");
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const refresh = async () => {
    setBusy(true);
    try {
      await app.refreshData();
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title={l("诊断与恢复", "Diagnostics and recovery")}
        subtitle={l(
          "只在本机运行检查，不调用模型。复制诊断信息不含 Key、会话原文或代码。",
          "Checks run locally and never call a model. Copied diagnostics contain no keys, full sessions, or code.",
        )}
        actions={
          <>
            <Button variant="ghost" icon="refresh" onClick={refresh} loading={busy}>
              {l("重新检查", "Recheck")}
            </Button>
            <CopyButton text={sanitized()} label={l("复制诊断信息", "Copy diagnostics")} />
          </>
        }
      />
      {error ? <ErrorBanner error={app.handleError(error)} /> : null}

      <div className="stat-grid mb-24">
        <div className="stat stat-accent">
          <span className="stat-value">{diag?.app_version || "—"}</span>
          <span className="stat-label">harness2-morphd</span>
        </div>
        <div className="stat">
          <span className="stat-value">{diag?.disk.files ?? "—"}</span>
          <span className="stat-label">{l("本地文件", "Local files")}</span>
        </div>
        <div className="stat">
          <span className="stat-value">{formatBytes(diag?.disk.bytes || 0, lang)}</span>
          <span className="stat-label">{l("磁盘占用", "Disk usage")}</span>
        </div>
        <div className="stat">
          <span className="stat-value">{app.status?.hosts.length || 0}</span>
          <span className="stat-label">{l("已适配宿主", "Adapted hosts")}</span>
        </div>
      </div>

      <Section
        title={l("检查项", "Checks")}
        subtitle={l("失败项会给出可操作建议；不会在这里展示堆栈。", "Failures include an actionable next step, never a stack trace.")}
      >
        <div className="list">
          {(diag?.checks || []).map((check) => {
            const copy = checkCopy(check);
            return (
            <div className="list-item" key={check.name}>
              <div className="list-main">
                <span className="list-title">{copy.name}</span>
                <span className="sub">{copy.detail}</span>
              </div>
              <StatusMark tone={check.ok ? "ok" : "bad"}>
                {check.ok ? l("通过", "Pass") : l("需要处理", "Needs attention")}
              </StatusMark>
            </div>
            );
          })}
        </div>
      </Section>

      <div className="grid-2 mb-24">
        <Section title={l("环境", "Environment")} subtitle={l("本地路径与权限。", "Local paths and permissions.")} className="mb-0">
          <div className="stack-sm">
            <KeyValue label={l("数据目录", "Data directory")}><span className="mono small wrap-anywhere">{diag?.home || "—"}</span></KeyValue>
            <KeyValue label={l("目录权限", "Directory permissions")}>
              {diag?.permissions_ok ? l("仅当前用户（0700）", "Current user only (0700)") : l("需要检查", "Check required")}
            </KeyValue>
            <KeyValue label={l("Key 存储", "Key storage")}>
              {app.providers?.vault.keychain ? l("系统 Keychain", "OS keychain") : l("加密保险库", "Encrypted vault")}
              {" · "}
              {app.providers?.vault.vault_unlocked ? l("已解锁", "unlocked") : l("已锁定", "locked")}
            </KeyValue>
            <KeyValue label={l("本地服务启动", "Service started")}>{formatDate(app.status?.started_at, lang)}</KeyValue>
            <KeyValue label={l("遥测", "Telemetry")}>{app.settings?.privacy.telemetry ? l("开", "On") : l("关", "Off")}</KeyValue>
          </div>
        </Section>
        <Section title={l("模型连通性", "Model connectivity")} subtitle={l("已保存的服务商状态；连接测试在模型页手动执行。", "Saved provider status; connection tests run manually on Models.")} className="mb-0">
          <div className="list">
            {(diag?.providers || []).map((provider) => (
              <div className="list-item" key={provider.id}>
                <div className="list-main">
                  <span className="list-title">{provider.label}</span>
                  <span className="sub mono">{provider.model || "—"}</span>
                </div>
                <Badge variant={provider.key_configured ? "accent" : "dashed"}>
                  {provider.key_configured
                    ? l("Key 已配置", "Key configured")
                    : provider.local
                      ? l("本地模型", "Local model")
                      : l("未配置 Key", "No key")}
                </Badge>
              </div>
            ))}
            {(diag?.providers || []).length === 0 ? <p className="sub">{l("还没有服务商。", "No providers yet.")}</p> : null}
          </div>
        </Section>
      </div>

      <Section
        title={l("安全模式与重置", "Safety mode and reset")}
        subtitle={l("安全模式不删除历史，只停止自动行为；重置学习会删除建议、任务与经验，保留 Key 和设置。", "Safety mode keeps history and stops automatic behavior. Reset learning deletes suggestions, jobs, and experience while keeping keys and settings.")}
      >
        <div className="stack">
          <div className="danger-action">
            <div>
              <strong>{l("开启安全模式", "Enable safety mode")}</strong>
              <p className="sub">
                {l(
                  "关闭自动学习与自动应用，暂停所有分析；仍可在历史中查看与手动处理。适合出现异常或安全担忧时。",
                  "Disables automatic learning and apply, pausing analysis. History and manual actions remain. Use it on anomalies or security concerns.",
                )}
              </p>
            </div>
            <Button variant="danger" icon="shield" onClick={() => setSafety(true)} loading={busy}>
              {l("开启安全模式", "Enable safety mode")}
            </Button>
          </div>
          <div className="danger-action">
            <div>
              <strong>{l("重置学习", "Reset learning")}</strong>
              <p className="sub">
                {l(
                  "删除建议、diff、任务与试用观察；不会回退已应用的 harness 文件，也不会删除 Key、设置、会话或快照。",
                  "Deletes suggestions, diffs, jobs, and trial observations. Applied harness files, keys, settings, sessions, and snapshots remain.",
                )}
              </p>
            </div>
            <Button variant="danger" icon="undo" onClick={() => setResetOpen(true)}>
              {l("重置学习", "Reset learning")}
            </Button>
          </div>
          <InfoNote icon="undo">
            {l(
              "想把 harness 本身恢复到最初状态，请到宿主页使用“恢复原始 harness”；它会使用最早的快照并检测手动修改。",
              "To restore the harness itself, use Restore original harness on Hosts; it uses the earliest snapshot and checks for manual edits.",
            )}
          </InfoNote>
        </div>
      </Section>

      <Section
        title={l("脱敏日志", "Redacted logs")}
        subtitle={l("默认滚动上限 100MB；不包含会话原文、文件名或 Key。日志保留最近事件用于排障。", "Rolls at 100MB; no full sessions, filenames, or keys. Recent events are kept for troubleshooting.")}
        actions={<CopyButton text={sanitized()} label={l("复制脱敏诊断", "Copy diagnostics")} />}
      >
        <div className="code-wrap">
          <pre className="code-block" tabIndex={0}>
            {(diag?.logs_tail || [])
              .slice(-40)
              .reverse()
              .map((entry) => JSON.stringify(entry))
              .join("\n") || l("（暂无日志）", "(no logs yet)")}
          </pre>
        </div>
      </Section>

      {safety ? (
        <ConfirmDialog
          title={l("开启安全模式？", "Enable safety mode?")}
          confirmLabel={l("关闭自动学习与应用", "Disable learning and auto-apply")}
          onConfirm={enableSafety}
          onClose={() => setSafety(false)}
          danger
          busy={busy}
        >
          <p>{l("这不会撤销已应用的改动，也不会删除数据；它会停止后续自动分析。", "This does not revert applied changes or delete data; it stops future automatic analysis.")}</p>
          <p className="sub">{l("恢复时重新打开“启用自动学习”即可。", "To recover, turn Enable automatic learning back on.")}</p>
        </ConfirmDialog>
      ) : null}

      {resetOpen ? (
        <ConfirmDialog
          title={l("重置学习记录？", "Reset learning data?")}
          confirmLabel={l("删除建议与任务", "Delete suggestions and jobs")}
          onConfirm={resetLearning}
          onClose={() => setResetOpen(false)}
          danger
          busy={busy}
        >
          <p>{l("此操作不可恢复；Key、设置与会话记录保留。", "This cannot be undone. Keys, settings, and session records are kept.")}</p>
          <p className="sub">
            {l(
              "如果你还想让 harness 回到最初状态，请在宿主页执行“恢复原始 harness”。",
              "If you also want the harness restored, use Restore original harness on Hosts.",
            )}
          </p>
        </ConfirmDialog>
      ) : null}
    </>
  );
}
