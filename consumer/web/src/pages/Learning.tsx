import { useState } from "react";
import { api } from "../api";
import {
  Button,
  ConfirmDialog,
  ErrorBanner,
  Field,
  InfoNote,
  Input,
  Modal,
  PageHeader,
  RadioCards,
  Section,
  Select,
  Toggle,
} from "../components/UI";
import { formatMoney, useL, useLang } from "../i18n";
import { useApp } from "../state";
import type { ProjectSettings, Settings } from "../types";

const APPETITE_LIMITS = {
  conservative: { files: 1, lines: 30, zh: "只动现有说明书，不新建脚本。", en: "Only the existing instruction file; no new scripts." },
  balanced: { files: 3, lines: 100, zh: "可以补技能和小脚本，最多 3 个文件。", en: "Instructions, skills, and small scripts; up to 3 files." },
  bold: { files: 10, lines: 300, zh: "允许更大范围尝试，最多 10 个文件。", en: "A wider change, up to 10 files." },
} as const;

const TRUST_HUMAN = {
  observer: { zh: "只看不改。我会总结，但不会动你的任何文件。", en: "Observe only. I summarize but never touch your files." },
  advisor: { zh: "先给我看。你点应用我才改，改完随时能退。", en: "You review first. Nothing changes until you apply; revert anytime." },
  assistant: { zh: "小事自己改。只改说明/小脚本，改完告诉你，7 天内可退。", en: "Small safe changes apply themselves. You are told, and can revert within 7 days." },
  autopilot: { zh: "安全范围内自己改。权限和敏感文件永远不碰，所有改动有记录、可撤销。", en: "Safe changes auto-apply. Permissions and sensitive files are never touched; everything is recorded and reversible." },
} as const;

export function Learning() {
  const app = useApp();
  const l = useL();
  const lang = useLang();
  const settings = app.settings;
  const [error, setError] = useState<unknown>(null);
  const [confirmAuto, setConfirmAuto] = useState(false);
  const [confirmTrust, setConfirmTrust] = useState<Settings["trust_level"] | null>(null);
  const [confirmAppetite, setConfirmAppetite] = useState<Settings["change_appetite"] | null>(null);
  const [projectEditor, setProjectEditor] = useState<ProjectSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const electron = Boolean((window as unknown as { morphd?: unknown }).morphd);

  if (!settings) return null;

  const save = async (patch: Partial<Settings>) => {
    setError(null);
    const saved = await app.saveSettings(patch);
    if (!saved) return false;
    await app.refreshAll(true);
    return true;
  };

  const saveCadence = (patch: Partial<Settings["cadence"]>) =>
    save({ cadence: { ...settings.cadence, ...patch } });
  const saveTrust = (trust: Settings["trust_level"]) => {
    const patch: Partial<Settings> = {
      trust_level: trust,
      auto_apply: trust === "observer" || trust === "advisor" ? false : settings.auto_apply,
    };
    if (trust === "autopilot") {
      patch.auto_apply = true;
      patch.autopilot_ack = true;
      patch.undo_days = Math.max(30, settings.undo_days);
      patch.change_appetite = "conservative";
    }
    if (trust === "assistant" && settings.change_appetite !== "conservative") {
      patch.autopilot_ack = true;
    }
    return save(patch);
  };
  const chooseAppetite = (appetite: Settings["change_appetite"]) => {
    if (settings.trust_level === "assistant" && appetite !== "conservative") {
      setConfirmAppetite(appetite);
      return;
    }
    if (settings.trust_level === "autopilot") return;
    void save({ change_appetite: appetite });
  };
  const setAutoApply = async (enabled: boolean) => {
    if (enabled) {
      setConfirmAuto(true);
      return;
    }
    await save({ auto_apply: false });
  };
  const enableAuto = async () => {
    setBusy(true);
    const ok = await save({ auto_apply: true, autopilot_ack: true });
    setBusy(false);
    if (ok) {
      setConfirmAuto(false);
      app.toast("ok", "自动应用已开启，附带快照与自动回滚", "Auto-apply enabled with snapshots and rollback");
    }
  };
  const chooseTrust = async (trust: Settings["trust_level"]) => {
    if ((trust === "assistant" || trust === "autopilot") && settings.trust_level !== trust) {
      setConfirmTrust(trust);
      return;
    }
    await saveTrust(trust);
  };
  const confirmTrustChange = async () => {
    if (!confirmTrust) return;
    setBusy(true);
    const ok = await saveTrust(confirmTrust);
    setBusy(false);
    if (ok) {
      setConfirmTrust(null);
      app.toast("ok", "信任等级已更新", "Trust level updated");
    }
  };
  const pause = async (until: string) => {
    setBusy(true);
    try {
      await api.pause(until);
      await app.refreshAll(true);
      app.toast("ok", "自动学习已暂停", "Learning paused");
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };
  const resume = async () => {
    setBusy(true);
    try {
      const result = await api.resume();
      await app.refreshAll(true);
      const skipped = Number((result as { state?: { last_vacation_skipped?: number } }).state?.last_vacation_skipped || 0);
      app.toast(
        "ok",
        skipped > 0 ? `自动学习已恢复；休假期间未分析 ${skipped} 次会话` : "自动学习已恢复",
        skipped > 0 ? `Learning resumed; ${skipped} session(s) were skipped during vacation` : "Learning resumed",
      );
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };
  const vacation = async (active: boolean) => {
    setBusy(true);
    try {
      await api.vacation(active);
      await app.refreshAll(true);
      app.toast("ok", active ? "已进入休假模式" : "已结束休假", active ? "Vacation mode on" : "Vacation mode off");
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const limits = APPETITE_LIMITS[settings.change_appetite];
  const quiet = settings.cadence.quiet_hours;
  const learning = app.status?.learning;

  return (
    <>
      <PageHeader
        title={l("学习设置", "Learning settings")}
        subtitle={l(
          "默认顾问 + 保守；自动触发但只在有信号时分析；所有改动可解释、可预算、可撤销。",
          "Advisor + conservative by default; automatic triggering but analysis only on signal; every change is explainable, budgeted, and reversible.",
        )}
        actions={
          learning?.paused ? (
            <Button icon="play" onClick={resume} loading={busy}>
              {l("恢复学习", "Resume learning")}
            </Button>
          ) : (
            <Button variant="ghost" icon="pause" onClick={() => void pause("1d")} loading={busy}>
              {l("暂停 1 天", "Pause 1 day")}
            </Button>
          )
        }
      />
      {error ? <ErrorBanner error={app.handleError(error)} /> : null}

      <Section
        title={l("自动学习", "Automatic learning")}
        subtitle={l("会话结束即判断；没有信号时零成本跳过，不为了用满预算而分析。", "Every session is triaged; no signal means a zero-cost skip. harness2-morphd never analyzes just to use budget.")}
      >
        <div className="stack">
          <Toggle
            checked={settings.enabled}
            onChange={(next) => void save({ enabled: next })}
            label={l("启用自动学习", "Enable automatic learning")}
            hint={l("关闭后不再自动分析或建议；仍可手动查看历史。", "When off, no automatic analysis or suggestions; history remains available.")}
          />
          <div className="grid-2">
            <Field
              label={l("分析深度", "Analysis depth")}
              hint={l("自适应：只有失败/纠正/工具循环等信号才分析。轻量：只记录，不调用模型。完整：每次都递归（更贵）。", "Adaptive: analyze only on failures, corrections, or tool loops. Light: record only, no model. Full: recurse every session (more expensive).")}
              htmlFor="analysis-depth"
            >
              <Select
                id="analysis-depth"
                value={settings.analysis.depth}
                onChange={(event) => void save({ analysis: { ...settings.analysis, depth: event.target.value as Settings["analysis"]["depth"] } })}
              >
                <option value="adaptive">{l("自适应（推荐）", "Adaptive (recommended)")}</option>
                <option value="light">{l("轻量（零模型调用）", "Light (zero model calls)")}</option>
                <option value="full">{l("完整递归", "Full recursion")}</option>
              </Select>
            </Field>
            <div className="stack-sm">
              <span className="small strong">{l("触发规则", "Trigger")}</span>
              <ul className="step-list">
                <li><span>•</span><span>{l("每次非隐身、非暂停会话结束都会记录", "Every non-incognito, non-paused session is recorded")}</span></li>
                <li><span>•</span><span>{l("有失败/纠正/工具循环才进入分析", "Only failures, corrections, or tool loops enter analysis")}</span></li>
                <li><span>•</span><span>{l("顺利会话标记 noop，成本 $0", "Smooth sessions become noop at $0")}</span></li>
              </ul>
            </div>
          </div>
          <InfoNote icon="clock">
            {l(
              "宿主退出时分析会写检查点并暂停，下次启动继续；不会为了跑完分析而阻止宿主退出。",
              "On host exit, analysis checkpoints and pauses; it resumes next launch and never blocks host shutdown.",
            )}
          </InfoNote>
        </div>
      </Section>

      <Section
        title={l("信任等级与变更幅度", "Trust and change appetite")}
        subtitle={l("这决定 harness2-morphd 能自动做什么，以及一次最多改多少。", "This decides what harness2-morphd may do automatically and how much it may change at once.")}
      >
        <div className="stack">
          <RadioCards
            value={settings.trust_level}
            onChange={(next) => void chooseTrust(next)}
            label={l("信任等级", "Trust level")}
            options={[
              { value: "observer", title: l("观察", "Observer"), description: l(TRUST_HUMAN.observer.zh, TRUST_HUMAN.observer.en) },
              { value: "advisor", title: l("顾问（默认）", "Advisor (default)"), description: l(TRUST_HUMAN.advisor.zh, TRUST_HUMAN.advisor.en) },
              { value: "assistant", title: l("助手", "Assistant"), description: l(TRUST_HUMAN.assistant.zh, TRUST_HUMAN.assistant.en) },
              { value: "autopilot", title: l("自动驾驶", "Autopilot"), description: l(TRUST_HUMAN.autopilot.zh, TRUST_HUMAN.autopilot.en) },
            ]}
          />
          <RadioCards
            value={settings.change_appetite}
            onChange={chooseAppetite}
            disabled={settings.trust_level === "autopilot"}
            label={l("变更幅度", "Change appetite")}
            options={[
              { value: "conservative", title: l("保守（默认）", "Conservative (default)"), description: l(APPETITE_LIMITS.conservative.zh, APPETITE_LIMITS.conservative.en), detail: `${APPETITE_LIMITS.conservative.files} ${l("个文件 /", "file /")} ${APPETITE_LIMITS.conservative.lines} ${l("行", "lines")}` },
              { value: "balanced", title: l("均衡", "Balanced"), description: l(APPETITE_LIMITS.balanced.zh, APPETITE_LIMITS.balanced.en), detail: `${APPETITE_LIMITS.balanced.files} ${l("个文件 /", "files /")} ${APPETITE_LIMITS.balanced.lines} ${l("行", "lines")}` },
              { value: "bold", title: l("大胆", "Bold"), description: l(APPETITE_LIMITS.bold.zh, APPETITE_LIMITS.bold.en), detail: `${APPETITE_LIMITS.bold.files} ${l("个文件 /", "files /")} ${APPETITE_LIMITS.bold.lines} ${l("行", "lines")}` },
            ]}
          />
          <div className="grid-3">
            <div className="fact">
              <strong>{l("允许的表面", "Allowed surfaces")}</strong>
              {settings.change_appetite === "conservative"
                ? l("现有说明书", "Instruction file")
                : settings.change_appetite === "balanced"
                  ? l("说明书、技能、现有脚本", "Instructions, skills, existing scripts")
                  : l("说明书、技能、脚本、agent 文件", "Instructions, skills, scripts, agent files")}
            </div>
            <div className="fact">
              <strong>{l("文件/行数上限", "File/line limit")}</strong>
              {limits.files} / {limits.lines}
            </div>
            <div className="fact">
              <strong>{l("自动应用", "Auto-apply")}</strong>
              {settings.auto_apply ? l("已开启", "On") : l("关闭", "Off")}
            </div>
          </div>
          <InfoNote tone="warn" icon="shield">
            {l(
              "任何等级、任何幅度都永久禁止：修改权限/沙箱/approval/hooks/settings/MCP、读取或写入凭据、访问 workspace 之外的路径。",
              "Every level and appetite permanently forbids: permission/sandbox/approval/hooks/settings/MCP changes, credential access, and paths outside the workspace.",
            )}
          </InfoNote>
        </div>
      </Section>

      <Section
        title={l("应用与试用", "Apply and trial")}
        subtitle={l("默认只建议；自动应用需要显式开启，并保留快照、试用与自动回滚。", "Suggestions only by default. Auto-apply is explicit and keeps snapshots, a trial, and automatic rollback.")}
      >
        <div className="stack">
          <Toggle
            checked={settings.auto_apply}
            onChange={setAutoApply}
            label={l("自动应用通过安全扫描的建议", "Auto-apply suggestions that pass the safety scan")}
            hint={l(
              "助手等级只自动应用保守幅度下的说明书改动；自动驾驶始终使用保守幅度。",
              "Assistant auto-applies only instruction changes at conservative appetite; autopilot always uses conservative appetite.",
            )}
            disabled={settings.trust_level === "observer" || settings.trust_level === "advisor"}
            disabledReason={l("先把信任等级切到“助手”或“自动驾驶”。", "Switch trust to Assistant or Autopilot first.")}
          />
          <div className="grid-3">
            <Field label={l("试用会话数", "Trial sessions")} htmlFor="trial-sessions">
              <Input
                id="trial-sessions"
                type="number"
                min={1}
                max={10}
                value={settings.trial_sessions}
                onChange={(event) => void save({ trial_sessions: Math.max(1, Math.min(10, Number(event.target.value) || 1)) })}
              />
            </Field>
            <Field label={l("撤销窗口（天）", "Undo window (days)")} htmlFor="undo-days">
              <Input
                id="undo-days"
                type="number"
                min={1}
                max={30}
                value={settings.undo_days}
                disabled={settings.trust_level === "autopilot"}
                onChange={(event) => void save({ undo_days: Math.max(1, Math.min(30, Number(event.target.value) || 1)) })}
              />
            </Field>
            <Field label={l("自动应用时间窗", "Auto-apply window")} htmlFor="auto-window">
              <Select
                id="auto-window"
                value={settings.auto_apply_window}
                onChange={(event) => void save({ auto_apply_window: event.target.value as Settings["auto_apply_window"] })}
              >
                <option value="any">{l("任意时间", "Anytime")}</option>
                <option value="quiet">{l("仅静默时段", "Quiet hours only")}</option>
                <option value="custom">{l("自定义时段", "Custom window")}</option>
              </Select>
            </Field>
          </div>
          {settings.auto_apply_window === "custom" ? (
            <div className="quiet-row">
              <Input
                type="time"
                aria-label={l("自动应用开始", "Auto-apply start")}
                value={settings.cadence.auto_apply_window_start}
                onChange={(event) =>
                  void saveCadence({ auto_apply_window_start: event.target.value })
                }
              />
              <span aria-hidden="true">—</span>
              <Input
                type="time"
                aria-label={l("自动应用结束", "Auto-apply end")}
                value={settings.cadence.auto_apply_window_end}
                onChange={(event) =>
                  void saveCadence({ auto_apply_window_end: event.target.value })
                }
              />
            </div>
          ) : null}
          <Toggle
            checked={settings.auto_rollback}
            onChange={(next) => void save({ auto_rollback: next })}
            label={l("试用期出现回归时自动回滚", "Roll back automatically on trial regression")}
            hint={l("硬条件：安全失败、产物缺失、连续纠正/工具循环升高、用户反馈不好用。", "Hard signals: safety failure, missing deliverables, rising corrections/tool loops, negative user feedback.")}
            disabled={settings.auto_apply}
            disabledReason={settings.auto_apply ? l("开启自动应用时必须保留自动回滚。", "Automatic rollback is required while auto-apply is on.") : undefined}
          />
          <p className="sub">
            {l("当前预算下大约可做", "At the current budget, about")}{" "}
            {settings.budget.per_job_usd > 0
              ? Math.max(0, Math.floor(app.budget?.monthly.remaining ? app.budget.monthly.remaining / settings.budget.per_job_usd : 0))
              : 0}{" "}
            {l("次最坏情况分析；实际通常更少。", "worst-case analyses; actual usage is usually lower.")}
          </p>
        </div>
      </Section>

      <Section
        title={l("通知与工作节奏", "Notifications and cadence")}
        subtitle={l("分析与展示解耦：该分析照常进行，但只在你允许的时候展示。", "Analysis and presentation are decoupled: work happens, display waits for your rhythm.")}
      >
        <div className="stack">
          <Toggle
            checked={settings.cadence.focus_mode}
            onChange={(next) => void saveCadence({ focus_mode: next })}
            label={l("专注模式", "Focus mode")}
            hint={l("会话进行中不弹任何建议或通知，只进入待看。", "No suggestions or notifications during a session; everything goes to the inbox.")}
          />
          <div className="grid-2">
            <Field label={l("静默时段", "Quiet hours")} hint={l("时段内只入待看，不主动展示或通知。", "During this window suggestions stay in the inbox.")}>
              <div className="quiet-row">
                <Input
                  type="time"
                  aria-label={l("静默开始", "Quiet start")}
                  value={quiet?.[0] || "22:00"}
                  disabled={quiet === null}
                  onChange={(event) => void saveCadence({ quiet_hours: [event.target.value, quiet?.[1] || "08:00"] })}
                />
                <span aria-hidden="true">—</span>
                <Input
                  type="time"
                  aria-label={l("静默结束", "Quiet end")}
                  value={quiet?.[1] || "08:00"}
                  disabled={quiet === null}
                  onChange={(event) => void saveCadence({ quiet_hours: [quiet?.[0] || "22:00", event.target.value] })}
                />
                <Button size="sm" variant="ghost" onClick={() => void saveCadence({ quiet_hours: quiet === null ? ["22:00", "08:00"] : null })}>
                  {quiet === null ? l("启用", "Enable") : l("关闭", "Disable")}
                </Button>
              </div>
            </Field>
            <Field label={l("每周主动建议上限", "Weekly proactive cap")} hint={l("超出的建议进入待看，不会丢失。", "Extra suggestions stay in the inbox and are never lost.")} htmlFor="weekly-cap">
              <Select
                id="weekly-cap"
                value={settings.cadence.weekly_cap}
                onChange={(event) => void saveCadence({ weekly_cap: Number(event.target.value) })}
              >
                {Array.from({ length: 11 }, (_, index) => (
                  <option key={index} value={index}>
                    {index === 0 ? l("不主动展示", "No proactive display") : index}
                  </option>
                ))}
              </Select>
            </Field>
          </div>
          <div className="grid-2">
            <Field label={l("摘要频率", "Digest frequency")} hint={l("摘要只汇总，不触发新的系统通知。", "Digests summarize and never create extra system notifications.")} htmlFor="digest">
              <Select
                id="digest"
                value={settings.cadence.digest}
                onChange={(event) => void saveCadence({ digest: event.target.value as Settings["cadence"]["digest"] })}
              >
                <option value="off">{l("即时展示所有高/普通置信度", "Show all immediately")}</option>
                <option value="daily">{l("每日摘要（仅高置信度即时）", "Daily digest (high confidence immediate)")}</option>
                <option value="weekly">{l("每周摘要（仅高置信度即时）", "Weekly digest (high confidence immediate)")}</option>
                <option value="only_inbox">{l("仅放入待看", "Inbox only")}</option>
              </Select>
            </Field>
            <Field label={l("空闲后再提示（分钟）", "Idle delay (minutes)")} hint={l("0 表示会话结束即展示；后端不会因此常驻。", "0 means show when the session ends; the backend never stays resident for this.")} htmlFor="idle-delay">
              <Input
                id="idle-delay"
                type="number"
                min={0}
                max={30}
                value={settings.cadence.idle_delay_minutes}
                onChange={(event) => void saveCadence({ idle_delay_minutes: Math.max(0, Math.min(30, Number(event.target.value) || 0)) })}
              />
            </Field>
          </div>
          <Toggle
            checked={settings.cadence.notification_sound}
            onChange={(next) => void saveCadence({ notification_sound: next })}
            label={l("通知声音", "Notification sound")}
            hint={l("默认关；开启后仍只在宿主运行时且受静默时段限制。", "Off by default; only while the host runs and subject to quiet hours.")}
            disabled={!electron || !settings.cadence.system_notifications}
            disabledReason={!electron ? l("浏览器路径不请求系统通知权限。", "The browser path does not request notification permission.") : undefined}
          />
          <Toggle
            checked={settings.cadence.system_notifications}
            onChange={(next) => void saveCadence({ system_notifications: next })}
            label={l("系统通知（默认关）", "System notifications (off by default)")}
            hint={electron
              ? l("仅高置信度建议、仅在宿主运行时、受静默/上限约束。", "High-confidence suggestions only, only while the host runs, subject to quiet hours and caps.")
              : l("浏览器路径不发送系统通知；需要 Electron 桌面壳。", "The browser path cannot send system notifications; use the Electron desktop shell.")}
            disabled={!electron}
            disabledReason={!electron ? l("当前是浏览器路径，不会请求系统通知权限。", "This is the browser path and will not request notification permission.") : undefined}
          />
          <div className="grid-2">
            <Toggle
              checked={settings.cadence.tray_enabled}
              onChange={(next) => void saveCadence({ tray_enabled: next })}
              label={l("显示运行时托盘（桌面环境）", "Show a runtime tray (desktop)")}
              hint={l(
                "只在宿主运行时存在；无桌面/无托盘依赖时自动降级为浏览器 UI，不伪造托盘。",
                "Exists only while the host runs; without a desktop or tray dependencies it degrades to the browser UI.",
              )}
            />
            <Toggle
              checked={settings.cadence.start_minimized}
              onChange={(next) => void saveCadence({ start_minimized: next })}
              label={l("启动时最小化到托盘", "Start minimized to tray")}
              hint={l("仅桌面壳；宿主启动时仍会显示一次主页。", "Desktop shell only; the home page still shows once at host startup.")}
              disabled={!electron}
            />
          </div>
          <div className="grid-2">
            <Toggle
              checked={settings.cadence.suggestion_badge}
              onChange={(next) => void saveCadence({ suggestion_badge: next })}
              label={l("新建议轻微圆点提示", "Subtle dot for new suggestions")}
              hint={l("不使用数字徽标、红点堆积或闪烁。", "No numeric badges, red-dot piles, or flashing.")}
              disabled={!electron}
            />
          </div>
          <div className="grid-2">
            <Field label={l("关闭窗口时", "When closing the window")} hint={l("仅桌面壳；不会提供“退出宿主”。", "Desktop shell only; there is no “quit host” option.")} htmlFor="close-window">
              <Select
                id="close-window"
                value={settings.cadence.close_window}
                onChange={(event) => void saveCadence({ close_window: event.target.value as Settings["cadence"]["close_window"] })}
                disabled={!electron}
              >
                <option value="tray">{l("隐藏到托盘", "Hide to tray")}</option>
                <option value="exit">{l("退出界面", "Quit the UI")}</option>
              </Select>
            </Field>
            <div className="stack-sm">
              <span className="small strong">{l("暂停与休假", "Pause and vacation")}</span>
              <div className="row">
                <Button size="sm" variant="ghost" onClick={() => void pause("1d")} disabled={busy}>
                  {l("暂停 1 天", "Pause 1d")}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => void pause("1w")} disabled={busy}>
                  {l("暂停 1 周", "Pause 1w")}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => void vacation(!learning?.vacation)} disabled={busy}>
                  {learning?.vacation ? l("结束休假", "End vacation") : l("休假模式", "Vacation")}
                </Button>
              </div>
              <p className="sub">
                {l("暂停/休假期间不分析、不补跑、不产生费用；恢复时只显示汇总。", "No analysis, catch-up, or cost while paused or on vacation; resume shows a summary only.")}
              </p>
            </div>
          </div>
        </div>
      </Section>

      <Section
        title={l("表达与可访问性", "Language and accessibility")}
        subtitle={l("这些设置只改变呈现方式，不改变分析、预算或安全策略。", "These settings only change presentation, never analysis, budget, or safety policy.")}
      >
        <div className="stack">
          <div className="grid-3">
            <Field label={l("语言", "Language")} htmlFor="ui-language">
              <Select
                id="ui-language"
                value={settings.ui.language}
                onChange={(event) => void save({ ui: { ...settings.ui, language: event.target.value as Settings["ui"]["language"] } })}
              >
                <option value="system">{l("跟随系统", "Follow system")}</option>
                <option value="zh">中文</option>
                <option value="en">English</option>
              </Select>
            </Field>
            <Field label={l("主题", "Theme")} htmlFor="ui-theme">
              <Select
                id="ui-theme"
                value={settings.ui.theme}
                onChange={(event) => void save({ ui: { ...settings.ui, theme: event.target.value as Settings["ui"]["theme"] } })}
              >
                <option value="system">{l("跟随系统", "Follow system")}</option>
                <option value="light">{l("浅色", "Light")}</option>
                <option value="dark">{l("深色", "Dark")}</option>
              </Select>
            </Field>
            <Field label={l("字号", "Font size")} htmlFor="ui-font">
              <Select
                id="ui-font"
                value={settings.ui.font_size}
                onChange={(event) => void save({ ui: { ...settings.ui, font_size: event.target.value as Settings["ui"]["font_size"] } })}
              >
                <option value="small">{l("小", "Small")}</option>
                <option value="medium">{l("中", "Medium")}</option>
                <option value="large">{l("大", "Large")}</option>
                <option value="xlarge">{l("特大", "Extra large")}</option>
              </Select>
            </Field>
          </div>
          <div className="grid-3">
            <Field label={l("建议风格", "Suggestion style")} htmlFor="ui-style">
              <Select
                id="ui-style"
                value={settings.ui.style}
                onChange={(event) => void save({ ui: { ...settings.ui, style: event.target.value as Settings["ui"]["style"] } })}
              >
                <option value="concise">{l("简洁", "Concise")}</option>
                <option value="detailed">{l("详细", "Detailed")}</option>
              </Select>
            </Field>
            <Field label={l("减少动画", "Reduced motion")} htmlFor="ui-motion">
              <Select
                id="ui-motion"
                value={settings.ui.reduced_motion}
                onChange={(event) => void save({ ui: { ...settings.ui, reduced_motion: event.target.value as Settings["ui"]["reduced_motion"] } })}
              >
                <option value="system">{l("跟随系统", "Follow system")}</option>
                <option value="on">{l("开启", "On")}</option>
                <option value="off">{l("关闭", "Off")}</option>
              </Select>
            </Field>
            <Field label={l("对比度", "Contrast")} htmlFor="ui-contrast">
              <Select
                id="ui-contrast"
                value={settings.ui.contrast}
                onChange={(event) => void save({ ui: { ...settings.ui, contrast: event.target.value as Settings["ui"]["contrast"] } })}
              >
                <option value="normal">{l("标准", "Normal")}</option>
                <option value="high">{l("高对比度", "High contrast")}</option>
              </Select>
            </Field>
          </div>
          <Toggle
            checked={settings.ui.beginner}
            onChange={(next) => void save({ ui: { ...settings.ui, beginner: next } })}
            label={l("新手模式（用生活语言解释）", "Beginner mode (plain language)")}
            hint={l("主文案不出现内部术语；必需术语有解释。", "Primary copy avoids internal jargon; required terms are explained.")}
          />
          <Toggle
            checked={settings.ui.terminology_tooltips}
            onChange={(next) => void save({ ui: { ...settings.ui, terminology_tooltips: next } })}
            label={l("术语提示", "Terminology tooltips")}
            hint={l("必需术语使用下划虚线，可查看简短解释。", "Required terms are dashed-underlined with a short explanation.")}
          />
          <Toggle
            checked={settings.ui.screen_reader_optimized}
            onChange={(next) => void save({ ui: { ...settings.ui, screen_reader_optimized: next } })}
            label={l("屏幕阅读器优化", "Screen-reader optimization")}
            hint={l("为图标补充文本、保留状态播报与完整表格文本等价物。", "Adds text for icons and keeps status announcements and table text equivalents.")}
          />
          <Field
            label={l("快照保留数量（每个项目）", "Snapshots kept per project")}
            hint={l("默认保留最近 10 份；最早的一份始终保留用于恢复原始工作方式。", "Keeps the latest 10 by default; the earliest is always kept for Restore original.")}
            htmlFor="snapshot-limit"
          >
            <Input
              id="snapshot-limit"
              type="number"
              min={5}
              max={50}
              value={settings.privacy.snapshot_limit}
              onChange={(event) =>
                void save({
                  privacy: {
                    ...settings.privacy,
                    snapshot_limit: Math.max(5, Math.min(50, Number(event.target.value) || 10)),
                  },
                })
              }
            />
          </Field>
          <p className="sub">
            {l(
              "日期、货币与按键提示自动跟随语言；键盘焦点、屏幕阅读器语义和减少动画由系统设置与上方选项共同决定。",
              "Dates, currency, and keyboard hints follow the language. Keyboard focus, screen-reader semantics, and reduced motion follow the system and these options.",
            )}
          </p>
        </div>
      </Section>

      <Section
        title={l("项目边界", "Project boundaries")}
        subtitle={l("每个项目可独立关闭自动学习、标记敏感、限定本地模型或排除目录。", "Each project can disable learning, be marked sensitive, require local models, or exclude folders.")}
        actions={
          <Button size="sm" variant="ghost" onClick={() => void app.refreshData()}>
            {l("刷新项目", "Refresh projects")}
          </Button>
        }
      >
        {app.projects.length === 0 ? (
          <p className="sub">{l("还没有项目记录；第一次会话后会自动识别。", "No project yet; it is identified after the first session.")}</p>
        ) : (
          <div className="list">
            {app.projects.map((project) => (
              <div className="list-item" key={project.id}>
                <div className="list-main">
                  <span className="list-title">{project.domain} · {project.id}</span>
                  <span className="sub mono wrap-anywhere">{project.root}</span>
                  <span className="sub">
                    {project.enabled ? l("自动学习开启", "Learning on") : l("自动学习关闭", "Learning off")} ·{" "}
                    {project.sensitive ? l("敏感项目", "Sensitive") : l("普通项目", "Normal")} ·{" "}
                    {project.local_only ? l("仅本地模型", "Local only") : l("允许云端", "Cloud allowed")}
                    {project.excluded_globs.length ? ` · ${l("排除", "excluded")} ${project.excluded_globs.length}` : ""}
                  </span>
                </div>
                <div className="list-actions">
                  <Button size="sm" variant="ghost" onClick={() => setProjectEditor(project)}>
                    {l("编辑", "Edit")}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {confirmAuto ? (
        <ConfirmDialog
          title={l("开启自动应用？", "Enable auto-apply?")}
          confirmLabel={l("确认开启", "Enable")}
          onConfirm={enableAuto}
          onClose={() => setConfirmAuto(false)}
          busy={busy}
        >
          <p>
            {l(
              "开启后，通过安全检查的低风险改动会直接写入你的 Agent 工作方式文件，并进入试用观察。",
              "Safe, low-risk changes will be written directly into your agent files and enter a trial.",
            )}
          </p>
          <ul className="step-list">
            <li><span>•</span><span>{l("应用前自动快照", "A snapshot is created before every apply")}</span></li>
            <li><span>•</span><span>{l("试用期出现回归会自动回滚", "Regression during the trial rolls back automatically")}</span></li>
            <li><span>•</span><span>{l("权限/沙箱/hooks/settings/MCP 永远不碰", "Permissions/sandbox/hooks/settings/MCP are never touched")}</span></li>
            <li><span>•</span><span>{l("仍可在历史中随时撤销", "You can revert from History at any time")}</span></li>
          </ul>
          <p className="sub">
            {l("当前预算：", "Current budget: ")}
            {formatMoney(settings.budget.monthly_usd, lang)}
            {l(" / 月；单次分析上限 ", " / month; per-analysis ")}
            {formatMoney(settings.budget.per_job_usd, lang)}
          </p>
        </ConfirmDialog>
      ) : null}

      {confirmTrust ? (
        <ConfirmDialog
          title={l("切换信任等级？", "Change trust level?")}
          confirmLabel={l("确认切换", "Confirm")}
          onConfirm={confirmTrustChange}
          onClose={() => setConfirmTrust(null)}
          busy={busy}
        >
          <p>{l(TRUST_HUMAN[confirmTrust].zh, TRUST_HUMAN[confirmTrust].en)}</p>
          <p className="sub">
            {l(
              "它不能做：修改权限/沙箱/hooks/settings/MCP、读取凭据、访问 workspace 之外的路径。所有应用都有快照与撤销入口。",
              "It cannot: change permissions/sandbox/hooks/settings/MCP, read credentials, or touch paths outside the workspace. Every apply has a snapshot and revert entry.",
            )}
          </p>
        </ConfirmDialog>
      ) : null}

      {confirmAppetite ? (
        <ConfirmDialog
          title={l("扩大自动改动范围？", "Widen automatic changes?")}
          confirmLabel={l("确认幅度", "Confirm")}
          onConfirm={async () => {
            setBusy(true);
            const ok = await save({
              change_appetite: confirmAppetite,
              autopilot_ack: true,
            });
            setBusy(false);
            if (ok) {
              setConfirmAppetite(null);
              app.toast("ok", "变更幅度已更新", "Change appetite updated");
            }
          }}
          onClose={() => setConfirmAppetite(null)}
          busy={busy}
        >
          <p>
            {l(
              `在“助手”等级下选择${confirmAppetite === "balanced" ? "均衡" : "大胆"}幅度，低风险改动会自动应用的范围会扩大。`,
              `At the Assistant level, choosing ${confirmAppetite} widens what can auto-apply.`,
            )}
          </p>
          <p className="sub">
            {l(
              "仍然永久禁止修改权限、沙箱、hooks、settings、MCP 或读取凭据；每次自动应用都有快照、通知与撤销入口。",
              "Permission, sandbox, hooks, settings, MCP, and credential access remain permanently forbidden. Every auto-apply has a snapshot, notification, and revert entry.",
            )}
          </p>
        </ConfirmDialog>
      ) : null}

      {projectEditor ? (
        <ProjectEditor
          project={projectEditor}
          onClose={() => setProjectEditor(null)}
          onSaved={async () => {
            setProjectEditor(null);
            await app.refreshData();
            app.toast("ok", "项目设置已保存", "Project settings saved");
          }}
        />
      ) : null}
    </>
  );
}

function ProjectEditor({
  project,
  onClose,
  onSaved,
}: {
  project: ProjectSettings;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const app = useApp();
  const l = useL();
  const [form, setForm] = useState({
    enabled: project.enabled,
    sensitive: project.sensitive,
    local_only: project.local_only,
    domain: project.domain,
    retention_days: project.retention_days,
    excluded: project.excluded_globs.join("\n"),
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.saveProject(project.id, {
        enabled: form.enabled,
        sensitive: form.sensitive,
        local_only: form.local_only,
        domain: form.domain || "general",
        retention_days: form.retention_days,
        excluded_globs: form.excluded
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
      });
      await onSaved();
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title={l("项目设置", "Project settings")}
      subtitle={project.root}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {l("取消", "Cancel")}
          </Button>
          <Button onClick={save} loading={busy} icon="check">
            {l("保存项目设置", "Save project settings")}
          </Button>
        </>
      }
    >
      {error ? <ErrorBanner error={app.handleError(error)} /> : null}
      <Field label={l("领域（用于隔离经验与试用基线）", "Domain (isolates experience and trial baselines)")} htmlFor="project-domain">
        <Input id="project-domain" value={form.domain} onChange={(event) => setForm({ ...form, domain: event.target.value })} />
      </Field>
      <Toggle checked={form.enabled} onChange={(next) => setForm({ ...form, enabled: next })} label={l("自动学习此项目", "Automatic learning for this project")} />
      <Toggle
        checked={form.sensitive}
        onChange={(next) => setForm({ ...form, sensitive: next })}
        label={l("敏感项目", "Sensitive project")}
        hint={l("开启后关闭自动学习；只能手动触发，且发送前必须预览。", "Turns off automatic learning; only manual analysis with preview.")}
      />
      <Toggle
        checked={form.local_only}
        onChange={(next) => setForm({ ...form, local_only: next })}
        label={l("此项目仅使用本地模型", "Local models only for this project")}
        hint={l("阻止任何云端模型接触该项目内容。", "No cloud model may see this project's content.")}
      />
      <Field label={l("数据保留（天）", "Retention (days)")} htmlFor="project-retention">
        <Select id="project-retention" value={form.retention_days} onChange={(event) => setForm({ ...form, retention_days: Number(event.target.value) })}>
          <option value={7}>{l("7 天", "7 days")}</option>
          <option value={30}>{l("30 天", "30 days")}</option>
          <option value={90}>{l("90 天", "90 days")}</option>
          <option value={0}>{l("永久", "Forever")}</option>
        </Select>
      </Field>
      <Field
        label={l("排除目录 / glob（每行一个）", "Excluded folders / globs (one per line)")}
        hint={l("例如 secrets/**、customer-data/**；内容不会进入任务、预览或提案。", "For example secrets/** or customer-data/**. These never enter tasks, previews, or proposals.")}
        htmlFor="project-excluded"
      >
        <textarea
          id="project-excluded"
          className="input textarea"
          value={form.excluded}
          onChange={(event) => setForm({ ...form, excluded: event.target.value })}
          spellCheck={false}
        />
      </Field>
    </Modal>
  );
}
