import { useState } from "react";
import {
  Badge,
  Button,
  ConfirmDialog,
  ErrorBanner,
  Field,
  InfoNote,
  Input,
  PageHeader,
  Section,
  Segmented,
  Select,
  Toggle,
} from "../components/UI";
import { useL } from "../i18n";
import { useApp } from "../state";
import type { Settings } from "../types";

export function Advanced() {
  const app = useApp();
  const l = useL();
  const settings = app.settings;
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [egress, setEgress] = useState(settings?.enterprise.egress_allowlist.join(", ") || "");

  if (!settings) return null;

  const save = async (patch: Partial<Settings>) => {
    setError(null);
    setBusy(true);
    const saved = await app.saveSettings(patch);
    setBusy(false);
    if (saved) await app.refreshAll(true);
  };

  return (
    <>
      <PageHeader
        title={l("高级设置", "Advanced settings")}
        subtitle={l(
          "普通用户不需要改动这里。高级参数主要用于企业严格模式、诊断与统计门禁。",
          "Ordinary use needs nothing here. These options are for enterprise strict mode, diagnostics, and statistical gates.",
        )}
      />
      {error ? <ErrorBanner error={app.handleError(error)} /> : null}

      <Section
        title={l("递归参数", "Recursive parameters")}
        subtitle={l("默认 k=1、parallel；提高 k 会增加成本与耗时。", "Defaults are k=1 and parallel; higher k costs more and takes longer.")}
      >
        <div className="grid-2">
          <Field label={l("候选数量 k", "Candidate count k")} hint={l("消费者默认每次只提出一条最保守的改动。", "Consumer default proposes one conservative change.")} htmlFor="adv-k">
            <Select id="adv-k" value={settings.analysis.k} onChange={(event) => void save({ analysis: { ...settings.analysis, k: Number(event.target.value) } })} disabled={busy}>
              <option value={1}>1</option>
              <option value={2}>2</option>
              <option value={3}>3</option>
            </Select>
          </Field>
          <Field label={l("模式", "Modes")} hint={l("企业严格评测可使用 sequential。", "Enterprise strict evaluation may use sequential.")}>
            <Segmented
              value={settings.analysis.modes}
              onChange={(value) => void save({ analysis: { ...settings.analysis, modes: value } })}
              label={l("运行模式", "Run mode")}
              options={[
                { value: "parallel", label: "parallel" },
                { value: "sequential", label: "sequential" },
              ]}
            />
          </Field>
        </div>
        <InfoNote tone="warn" icon="alert">
          {l(
            "提高 k 会让最坏情况预算估算变大；如果超过月度/单次预算，分析会直接拒绝启动。",
            "Higher k increases the worst-case estimate; if it exceeds a limit, analysis is refused before starting.",
          )}
        </InfoNote>
      </Section>

      <Section
        title={l("企业严格模式", "Enterprise strict mode")}
        subtitle={l("启用双人审批、容器隔离与策略锁；不影响消费级默认。", "Dual approval, container isolation, and policy locks; consumer defaults are unchanged.")}
      >
        <div className="stack">
          <Toggle
            checked={settings.enterprise.strict}
            onChange={(next) => void save({ enterprise: { ...settings.enterprise, strict: next, dual_approval: next ? true : settings.enterprise.dual_approval } })}
            label={l("启用严格模式（双签 + 容器 + 策略锁）", "Enable strict mode (dual approval + container + policy locks)")}
            hint={l("影响 CLI/企业评测路径；消费级 Web UI 仍保持建议模式。", "Affects the CLI/enterprise evaluation path; the consumer Web UI remains suggestion-first.")}
          />
          <Toggle
            checked={settings.enterprise.container}
            onChange={(next) => void save({ enterprise: { ...settings.enterprise, container: next } })}
            label={l("强制容器隔离（严格模式）", "Require container isolation (strict mode)")}
            hint={l("引擎真实运行必须位于容器与 egress 白名单内；消费者默认使用宿主原生沙箱。", "Real engine runs must be inside a container with an egress allowlist; consumers use native host sandboxes by default.")}
          />
          <Field
            label={l("Egress allowlist（逗号分隔）", "Egress allowlist (comma separated)")}
            hint={l("严格模式容器防火墙只允许这些模型端点。", "The strict-mode container firewall allows only these model endpoints.")}
            htmlFor="adv-egress"
          >
            <Input
              id="adv-egress"
              value={egress}
              onChange={(event) => setEgress(event.target.value)}
              placeholder="api.deepseek.com, api.openai.com"
              spellCheck={false}
            />
          </Field>
          <Button
            variant="ghost"
            onClick={() =>
              void save({
                enterprise: {
                  ...settings.enterprise,
                  egress_allowlist: egress
                    .split(",")
                    .map((item) => item.trim())
                    .filter(Boolean),
                },
              })
            }
          >
            {l("保存 egress 白名单", "Save egress allowlist")}
          </Button>
          <div className="fact">
            <strong>{l("审批人", "Approvers")}</strong>
            {settings.enterprise.dual_approval ? l("P2 + P3 双签（CLI/企业）", "P2 + P3 (CLI/enterprise)") : l("消费级不需要", "Not needed for consumer")}
          </div>
        </div>
      </Section>

      <Section
        title={l("策略锁与永久禁止", "Policy locks and permanent prohibitions")}
        subtitle={l("无论信任等级与变更幅度如何，以下都不允许。", "Regardless of trust level or appetite, none of these are allowed.")}
      >
        <div className="grid-2">
          <ul className="step-list">
            <li><span>•</span><span>{l("修改权限/sandbox/approval", "Permission/sandbox/approval changes")}</span></li>
            <li><span>•</span><span>{l("修改 hooks/plugins/settings/MCP", "Hooks/plugins/settings/MCP changes")}</span></li>
            <li><span>•</span><span>{l("读取或写入凭据、私钥、.env", "Credential, private key, or .env access")}</span></li>
          </ul>
          <ul className="step-list">
            <li><span>•</span><span>{l("访问 workspace 之外的路径", "Paths outside the workspace")}</span></li>
            <li><span>•</span><span>{l("网络、子进程、动态执行", "Network, subprocess, dynamic execution")}</span></li>
            <li><span>•</span><span>{l("跨宿主共享候选或经验", "Sharing candidates/experience across hosts")}</span></li>
          </ul>
        </div>
        <InfoNote icon="shield">
          {l(
            "这些限制由候选静态扫描、surface 校验、路径检查与密钥扫描共同执行；被拒绝的候选零写入。",
            "Static scanning, surface checks, path checks, and secret scanning enforce these rules; rejected candidates write nothing.",
          )}
        </InfoNote>
      </Section>

      <Section
        title={l("恢复与危险操作", "Recovery and dangerous actions")}
        subtitle={l("重置设置不会删除 Key 或历史；删除数据请到隐私页。", "Resetting settings keeps keys and history; delete data on Privacy.")}
      >
        <div className="stack">
          <div className="danger-action">
            <div>
              <strong>{l("重置所有设置", "Reset all settings")}</strong>
              <p className="sub">
                {l(
                  "恢复顾问 + 保守、自动应用关闭、预算默认值、每周 2 条、静默时段默认。Key、宿主适配与历史保留。",
                  "Restores Advisor + Conservative, auto-apply off, default budget, weekly cap 2, and default quiet hours. Keys, host adaptation, and history remain.",
                )}
              </p>
            </div>
            <Button variant="danger" icon="undo" onClick={() => setResetOpen(true)}>
              {l("重置设置", "Reset settings")}
            </Button>
          </div>
          <div className="danger-action">
            <div>
              <strong>{l("恢复原始 harness", "Restore original harness")}</strong>
              <p className="sub">
                {l("使用最早的快照恢复文件，并检测手动修改；在宿主页执行。", "Restore the earliest snapshot and detect manual edits on Hosts.")}
              </p>
            </div>
            <Button variant="ghost" icon="undo" onClick={() => app.setPage("hosts")}>
              {l("去宿主页", "Open Hosts")}
            </Button>
          </div>
          <div className="danger-action">
            <div>
              <strong>{l("删除本地数据", "Delete local data")}</strong>
              <p className="sub">{l("会话、建议、任务、快照与日志；二次确认后不可恢复。", "Sessions, suggestions, jobs, snapshots, and logs; irreversible after confirmation.")}</p>
            </div>
            <Button variant="ghost" icon="trash" onClick={() => app.setPage("privacy")}>
              {l("去隐私页", "Open Privacy")}
            </Button>
          </div>
        </div>
      </Section>

      <Section title={l("版本与兼容", "Version and compatibility")} subtitle={l("宿主版本不在矩阵内时不会写入。", "Out-of-matrix host versions are never written to.")}>
        <div className="grid-3">
          <div className="fact"><strong>harness2-morphd</strong>{app.status?.app_version || "0.5.0"}</div>
          <div className="fact"><strong>Harness²</strong>1cfe254e8d25</div>
          <div className="fact"><strong>dsh</strong>0.2.1-rc.2</div>
          <div className="fact"><strong>Codex</strong>0.144.6</div>
          <div className="fact"><strong>Claude Code</strong>2.1.302</div>
          <div className="fact"><strong>License</strong>Apache-2.0</div>
        </div>
        <div className="row mt-16">
          <Badge variant="outline">{l("本地 API v1", "Local API v1")}</Badge>
          <Badge variant="outline">{l("数据目录 0700", "Data dir 0700")}</Badge>
          <Badge variant="outline">{l("无遥测默认", "Telemetry off by default")}</Badge>
        </div>
      </Section>

      {resetOpen ? (
        <ConfirmDialog
          title={l("重置所有设置？", "Reset all settings?")}
          confirmLabel={l("恢复默认设置", "Restore defaults")}
          onConfirm={async () => {
            setBusy(true);
            await app.resetSettings();
            setBusy(false);
            setResetOpen(false);
            await app.refreshData();
          }}
          onClose={() => setResetOpen(false)}
          danger
          busy={busy}
        >
          <p>{l("不会删除 Key、宿主适配、会话、建议或快照。", "Keys, host adaptation, sessions, suggestions, and snapshots are kept.")}</p>
          <p className="sub">{l("如果正在自动应用，重置会立即关闭它。", "If auto-apply is on, resetting turns it off immediately.")}</p>
        </ConfirmDialog>
      ) : null}
    </>
  );
}
