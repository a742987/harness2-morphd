import { useMemo, useState } from "react";
import { api } from "../api";
import { CandidateCard, CandidateDetailModal, candidateStateLabel } from "../components/Candidate";
import {
  Badge,
  Button,
  Card,
  ConfirmDialog,
  EmptyState,
  InfoNote,
  KeyValue,
  Modal,
  PageHeader,
  Progress,
  Section,
  Select,
  StatusMark,
} from "../components/UI";
import { formatMoney, timeAgo, useL, useLang } from "../i18n";
import { useApp } from "../state";
import type { Candidate, SessionRecord } from "../types";

const TRUST_LABELS: Record<string, { zh: string; en: string }> = {
  observer: { zh: "观察", en: "Observer" },
  advisor: { zh: "顾问", en: "Advisor" },
  assistant: { zh: "助手", en: "Assistant" },
  autopilot: { zh: "自动驾驶", en: "Autopilot" },
};

const SESSION_STATUS: Record<string, { zh: string; en: string }> = {
  recorded: { zh: "已记录", en: "Recorded" },
  queued: { zh: "已入队", en: "Queued" },
  analyzing: { zh: "分析中", en: "Analyzing" },
  analyzed: { zh: "已分析", en: "Analyzed" },
  applied: { zh: "已应用", en: "Applied" },
  noop: { zh: "无需调整", en: "No change needed" },
  paused: { zh: "已暂停", en: "Paused" },
  disabled: { zh: "自动学习已关", en: "Learning off" },
  project_disabled: { zh: "项目已禁用", en: "Project disabled" },
  sensitive_project: { zh: "敏感项目", en: "Sensitive project" },
  needs_confirm: { zh: "等待确认", en: "Needs confirmation" },
  failed: { zh: "失败", en: "Failed" },
  canceled: { zh: "已取消", en: "Canceled" },
};

function statusTone(status: string): "ok" | "warn" | "idle" | "bad" {
  if (["analyzed", "applied", "noop"].includes(status)) return "ok";
  if (["queued", "analyzing", "needs_confirm"].includes(status)) return "warn";
  if (["failed"].includes(status)) return "bad";
  return "idle";
}

function dayKey(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function barClass(value: number, max: number): string {
  if (max <= 0 || value <= 0) return "h-0";
  const bucket = Math.max(5, Math.min(100, Math.round((value / max) * 20) * 5));
  return `h-${bucket}`;
}

export function Home() {
  const app = useApp();
  const l = useL();
  const lang = useLang();
  const [detail, setDetail] = useState<string | null>(null);
  const [confirmApply, setConfirmApply] = useState<Candidate | null>(null);
  const [busy, setBusy] = useState(false);
  const [manual, setManual] = useState<{
    session: SessionRecord;
    preview: Record<string, unknown>;
    estimated?: number;
  } | null>(null);
  const [whyOpen, setWhyOpen] = useState(false);
  const [projectFilter, setProjectFilter] = useState("");
  const feedbackPrompts = app.status?.feedback?.prompts || [];

  const respondToFeedback = async (projectId: string, reason: string) => {
    setBusy(true);
    try {
      await api.respondFeedback(projectId, reason);
      await app.refreshAll(true);
      app.toast("ok", "已记录你的选择，之后恢复正常提示", "Preference saved; normal suggestions resume");
    } catch (caught) {
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const readyToShow = (candidate: Candidate) =>
    !candidate.present_after || new Date(candidate.present_after).getTime() <= Date.now();
  const visibleCandidates = app.candidates
    .filter(
      (candidate) =>
        !candidate.feedback?.some((item) => item.reason === "ignore") ||
        candidate.state !== "needs_apply",
    )
    .filter(readyToShow);
  const learning = app.status?.learning;
  const budget = app.budget;
  const currentProject = app.projects.find((project) => project.id === projectFilter) ||
    app.projects.find((project) => app.sessions.some((session) => session.project_id === project.id)) ||
    app.projects[0];
  const currentSessions = currentProject
    ? app.sessions.filter((session) => session.project_id === currentProject.id)
    : app.sessions;
  const currentCandidates = currentProject
    ? visibleCandidates.filter((candidate) => candidate.project_id === currentProject.id)
    : visibleCandidates;
  const inbox = currentCandidates.filter((candidate) => candidate.state === "needs_apply");

  const last7 = useMemo(() => {
    const days: { key: string; label: string; sessions: number; problems: number; cost: number }[] = [];
    for (let offset = 6; offset >= 0; offset -= 1) {
      const date = new Date();
      date.setDate(date.getDate() - offset);
      const key = dayKey(date);
      days.push({
        key,
        label: new Intl.DateTimeFormat(lang === "zh" ? "zh-CN" : "en-US", {
          weekday: "short",
        }).format(date),
        sessions: 0,
        problems: 0,
        cost: 0,
      });
    }
    for (const session of currentSessions) {
      const key = String(session.ended_at || "").slice(0, 10);
      const bucket = days.find((item) => item.key === key);
      if (!bucket) continue;
      bucket.sessions += 1;
      if ((session.signals?.corrections || 0) > 0 || session.signals?.achieved === false) {
        bucket.problems += 1;
      }
      bucket.cost += Number(session.cost_usd || 0);
    }
    for (const candidate of app.candidates) {
      const key = String(candidate.created_at || "").slice(0, 10);
      const bucket = days.find((item) => item.key === key);
      if (bucket) bucket.cost += Number(candidate.cost_usd || 0);
    }
    return days;
  }, [app.candidates, currentSessions, lang]);
  const maxProblems = Math.max(...last7.map((item) => item.problems), 1);
  const weekSessions = currentSessions.filter((session) => {
    const ended = new Date(session.ended_at).getTime();
    return Date.now() - ended < 7 * 86400000;
  });
  const weekCandidates = currentCandidates.filter((candidate) => {
    const created = new Date(candidate.created_at).getTime();
    return Date.now() - created < 7 * 86400000;
  });
  const applied = currentCandidates.filter((candidate) =>
    ["applied", "trial", "kept"].includes(candidate.state),
  );
  const reverted = currentCandidates.filter((candidate) => candidate.state === "reverted");
  const appliedThisWeek = weekCandidates.filter((candidate) => ["applied", "trial", "kept", "reverted"].includes(candidate.state));

  const pause = async (until: string) => {
    setBusy(true);
    try {
      await api.pause(until);
      await app.refreshAll(true);
      app.toast("ok", "自动学习已暂停", "Automatic learning paused");
    } catch (error) {
      const apiError = app.handleError(error);
      if (apiError) app.toast("warn", apiError.message, apiError.message);
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
        skipped > 0 ? `Learning resumed; ${skipped} session(s) were skipped during vacation` : "Automatic learning resumed",
      );
    } catch (error) {
      const apiError = app.handleError(error);
      if (apiError) app.toast("warn", apiError.message, apiError.message);
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
    } catch (error) {
      const apiError = app.handleError(error);
      if (apiError) app.toast("warn", apiError.message, apiError.message);
    } finally {
      setBusy(false);
    }
  };
  const incognito = async () => {
    setBusy(true);
    try {
      await api.incognito(!learning?.incognito_next);
      await app.refreshAll(true);
      app.toast(
        "ok",
        learning?.incognito_next ? "已取消下一次隐身" : "下一次会话将隐身",
        learning?.incognito_next ? "Next-session incognito off" : "Next session is incognito",
      );
    } catch (error) {
      const apiError = app.handleError(error);
      if (apiError) app.toast("warn", apiError.message, apiError.message);
    } finally {
      setBusy(false);
    }
  };
  const apply = async () => {
    if (!confirmApply) return;
    setBusy(true);
    try {
      await api.applyCandidate(confirmApply.id);
      await app.refreshData();
      setConfirmApply(null);
      app.toast("ok", "已应用，下一次会话生效；可随时撤销", "Applied; takes effect next session and can be reverted");
    } catch (error) {
      const apiError = app.handleError(error);
      if (apiError) {
        app.toast("warn", apiError.message, apiError.message);
        setConfirmApply(null);
      }
    } finally {
      setBusy(false);
    }
  };
  const ignore = async (candidate: Candidate) => {
    try {
      await api.feedback(candidate.id, "ignore");
      await app.refreshData();
      app.toast("info", "已移入待看；连续忽略会自动降频", "Moved to inbox; repeated ignores reduce frequency");
    } catch (error) {
      const apiError = app.handleError(error);
      if (apiError) app.toast("warn", apiError.message, apiError.message);
    }
  };
  const analyze = async (session: SessionRecord) => {
    try {
      const result = await api.analyzeSession(session.id, false);
      setManual({
        session,
        preview: result.preview || {},
        estimated: Number((result as { estimated_cost_usd?: number }).estimated_cost_usd ?? NaN),
      });
    } catch (error) {
      const apiError = app.handleError(error);
      if (apiError) app.toast("warn", apiError.message, apiError.message);
    }
  };
  const confirmAnalyze = async () => {
    if (!manual) return;
    setBusy(true);
    try {
      await api.analyzeSession(manual.session.id, true);
      await app.refreshData();
      setManual(null);
      app.toast("ok", "已加入分析队列", "Added to the analysis queue");
    } catch (error) {
      const apiError = app.handleError(error);
      if (apiError) app.toast("warn", apiError.message, apiError.message);
    } finally {
      setBusy(false);
    }
  };

  const why = () => {
    const reasons: string[] = [];
    if (!app.status?.hosts.length) reasons.push(l("还没有适配宿主。", "No host is adapted."));
    if (!app.settings?.models.proposer) reasons.push(l("还没有接入递归模型。", "No recursive model is connected."));
    if (learning?.paused) reasons.push(l("自动学习已暂停。", "Automatic learning is paused."));
    if (learning?.vacation) reasons.push(l("正在休假。", "Vacation mode is on."));
    if (!app.settings?.enabled) reasons.push(l("自动学习开关已关闭。", "The automatic learning switch is off."));
    if (budget?.blocked) reasons.push(l("本月预算已用完。", "This month's budget is exhausted."));
    if (app.status?.learning.focus) reasons.push(l("会话进行中：专注模式不展示建议。", "A session is active: focus mode hides suggestions."));
    if (app.sessions.every((session) => session.status === "noop")) reasons.push(l("最近会话没有需要改进的信号，零成本跳过。", "Recent sessions had no learning signal and were skipped at zero cost."));
    if (app.candidates.length === 0 && reasons.length === 0) reasons.push(l("还没有可展示的建议；harness2-morphd 不会为了凑数而分析。", "No suggestions to show; harness2-morphd does not analyze just to fill space."));
    return reasons;
  };

  return (
    <>
      <PageHeader
        title={l(`你好，今天想改哪一点？`, "What should get better today?")}
        subtitle={l(
          "harness2-morphd 在宿主里安静运行：每次会话结束后总结，只在有安全、可解释的改进时提醒你。",
          "harness2-morphd runs quietly inside your host: it summarizes each session and only surfaces safe, explainable improvements.",
        )}
        actions={
          <>
            <Button variant="ghost" icon="help" onClick={() => setWhyOpen(true)}>
              {l("为什么没有建议？", "Why no suggestion?")}
            </Button>
            {learning?.paused ? (
              <Button icon="play" onClick={resume} loading={busy}>
                {l("恢复学习", "Resume learning")}
              </Button>
            ) : (
              <Button icon="pause" onClick={() => void pause("1d")} loading={busy}>
                {l("暂停 1 天", "Pause 1 day")}
              </Button>
            )}
          </>
        }
      />

      {feedbackPrompts.length > 0 ? (
        <Section
          title={l("调整建议频率", "Tune suggestion frequency")}
          subtitle={l(
            "最近有建议被连续忽略。告诉我们原因，之后会恢复正常展示；你也可以随时改回来。",
            "Some suggestions were ignored repeatedly. Tell us why and normal delivery resumes; you can change this at any time.",
          )}
        >
          <div className="stack">
            {feedbackPrompts.map((prompt) => {
              const project = app.projects.find((item) => item.id === prompt.project_id);
              return (
                <div className="list-item" key={prompt.project_id || "default"}>
                  <div className="list-main">
                    <span className="list-title wrap-anywhere">
                      {project?.root || prompt.project_id || l("默认项目", "Default project")}
                    </span>
                    <span className="sub">
                      {l(
                        `连续忽略 ${prompt.counts.ignore || 0} 次；想怎样调整？`,
                        `Ignored ${prompt.counts.ignore || 0} times in a row. How should this be adjusted?`,
                      )}
                    </span>
                  </div>
                  <div className="row">
                    <Button
                      size="sm"
                      onClick={() => void respondToFeedback(prompt.project_id, "intrusive")}
                      loading={busy}
                    >
                      {l("安静 7 天", "Quiet 7 days")}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void respondToFeedback(prompt.project_id, "expensive")}
                      loading={busy}
                    >
                      {l("太贵", "Too expensive")}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void respondToFeedback(prompt.project_id, "not_useful")}
                      loading={busy}
                    >
                      {l("不相关", "Not relevant")}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => void respondToFeedback(prompt.project_id, "resume")}
                      loading={busy}
                    >
                      {l("继续正常提示", "Keep normal")}
                    </Button>
                  </div>
                </div>
              );
            })}
          </div>
        </Section>
      ) : null}

      <div className="hero mb-24">
        <div className="hero-main">
          <div className="spread">
            <Badge variant="inverse">
              {!app.status?.connected_hosts.length
                ? l("未连接宿主", "No host connected")
                : !app.settings?.enabled
                  ? l("自动学习已关闭", "Learning is off")
                  : learning?.incognito || learning?.incognito_next
                    ? l("隐身旁路生效", "Incognito active")
                  : learning?.paused
                    ? l("已暂停", "Paused")
                    : learning?.vacation
                      ? l("休假中", "Vacation")
                      : l("自动学习中", "Learning")}
            </Badge>
            {app.projects.length > 1 ? (
              <Select
                aria-label={l("切换项目", "Switch project")}
                value={currentProject?.id || ""}
                onChange={(event) => setProjectFilter(event.target.value)}
              >
                {app.projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {project.domain} · {project.id}
                  </option>
                ))}
              </Select>
            ) : (
              <span className="small">
                {currentProject
                  ? `${currentProject.domain} · ${currentProject.id}`
                  : l("默认项目", "Default project")}
              </span>
            )}
          </div>
          <h1>
            {inbox.length > 0
              ? l(`有 ${inbox.length} 条可改进建议`, `${inbox.length} improvement${inbox.length === 1 ? "" : "s"} waiting`)
              : l("目前没有需要你处理的建议", "Nothing needs your attention")}
          </h1>
          <p className="sub">
            {!app.status?.connected_hosts.length
              ? l(
                  "当前没有运行中的宿主连接。用适配后的 wrapper 启动宿主后，分析才会随宿主生命周期启动。",
                  "No running host is connected. Start the host through the adapted wrapper so analysis follows its lifecycle.",
                )
              : l(
                  "默认只建议，不自动改。应用前有快照，应用后随时可撤销。",
                  "Suggestions only by default. Every apply has a snapshot and can be reverted.",
                )}
          </p>
          <div className="row">
            <Button variant="secondary" icon="history" onClick={() => app.setPage("history")}>
              {l("查看待看与历史", "Open inbox and history")}
            </Button>
            <Button variant="ghost" icon="eye" onClick={incognito} loading={busy}>
              {learning?.incognito_next ? l("取消下一次隐身", "Cancel next-session incognito") : l("下一次会话隐身", "Incognito next session")}
            </Button>
            <Button
              variant="ghost"
              icon={app.settings?.enabled ? "pause" : "play"}
              onClick={() => void app.saveSettings({ enabled: !app.settings?.enabled })}
              loading={busy}
            >
              {app.settings?.enabled
                ? l("关闭自动学习", "Turn learning off")
                : l("启用自动学习", "Turn learning on")}
            </Button>
          </div>
          <div className="row">
            <button type="button" className="badge badge-inverse" onClick={() => void pause("1w")} disabled={busy}>
              {l("暂停 1 周", "Pause 1 week")}
            </button>
            <button type="button" className="badge badge-inverse" onClick={() => void pause("until_resume")} disabled={busy}>
              {l("一直暂停", "Pause until resumed")}
            </button>
            <button
              type="button"
              className="badge badge-inverse"
              onClick={() => void vacation(!learning?.vacation)}
              disabled={busy}
            >
              {learning?.vacation ? l("结束休假", "End vacation") : l("休假模式", "Vacation mode")}
            </button>
          </div>
        </div>
        <div className="hero-side">
          <Card>
            <div className="spread">
              <span className="small strong">{l("本月花费", "This month")}</span>
              <Badge>{l("估算", "Estimate")}</Badge>
            </div>
            <div className="stat-value">{formatMoney(budget?.monthly.spent || 0, lang)}</div>
            <Progress value={budget?.monthly.spent || 0} max={budget?.monthly.limit || 1} label={l("月度预算已用", "Monthly budget used")} />
            <p className="sub">
              {l("剩余", "Remaining")} {formatMoney(budget?.monthly.remaining || 0, lang)} / {l("上限", "limit")}{" "}
              {formatMoney(budget?.monthly.limit || 0, lang)}
            </p>
          </Card>
          <Card>
            <div className="spread">
              <span className="small strong">{l("信任等级", "Trust")}</span>
              <Badge variant="accent">{TRUST_LABELS[app.settings?.trust_level || "advisor"]?.[lang] || app.settings?.trust_level}</Badge>
            </div>
            <p className="sub mt-8">
              {app.settings?.trust_level === "observer"
                ? l("只看不改。", "Observe only.")
                : app.settings?.trust_level === "assistant"
                  ? l("小事自己改，其他先问。", "Small safe changes apply automatically; others ask.")
                  : app.settings?.trust_level === "autopilot"
                    ? l("安全范围内自动改，全部可撤销。", "Safe changes auto-apply; everything is reversible.")
                    : l("先给我看，你点应用才改。", "You review before anything is applied.")}
            </p>
            <Button size="sm" variant="ghost" onClick={() => app.setPage("learning")}>
              {l("调整", "Adjust")}
            </Button>
          </Card>
        </div>
      </div>

      <div className="stat-grid mb-24">
        <div className="stat stat-accent">
          <span className="stat-value">{currentSessions.length}</span>
          <span className="stat-label">{l("最近会话", "Recent sessions")}</span>
        </div>
        <div className="stat">
          <span className="stat-value">{weekSessions.length}</span>
          <span className="stat-label">{l("本周分析", "Analyzed this week")}</span>
        </div>
        <div className="stat">
          <span className="stat-value">{inbox.length}</span>
          <span className="stat-label">{l("待看建议", "Inbox")}</span>
        </div>
        <div className="stat">
          <span className="stat-value">{applied.length}</span>
          <span className="stat-label">{l("已应用", "Applied")}</span>
        </div>
      </div>

      <div className="grid-2 mb-24">
        <Section
          title={l("最近的改进建议", "Latest suggestions")}
          subtitle={l("默认只显示待看；应用后进入试用观察。", "Inbox by default; applied changes enter a trial.")}
          actions={
            <Button size="sm" variant="ghost" onClick={() => app.setPage("history")}>
              {l("全部历史", "All history")}
            </Button>
          }
          className="mb-0"
        >
          {inbox.length === 0 ? (
            <EmptyState
              icon="spark"
              title={l("这次不需要调整", "Nothing to adjust right now")}
              action={
                <Button size="sm" variant="ghost" onClick={() => setWhyOpen(true)}>
                  {l("为什么？", "Why?")}
                </Button>
              }
            >
              {l(
                "harness2-morphd 不会为了用满预算而分析。没有信号时会零成本跳过。",
                "harness2-morphd will not analyze just to use up budget. No signal means a zero-cost skip.",
              )}
            </EmptyState>
          ) : (
            <div className="stack">
              {inbox.slice(0, 3).map((candidate) => (
                <CandidateCard
                  key={candidate.id}
                  candidate={candidate}
                  compact
                  onOpen={() => setDetail(candidate.id)}
                  onApply={() => setConfirmApply(candidate)}
                  onIgnore={() => void ignore(candidate)}
                />
              ))}
            </div>
          )}
        </Section>
        <Section
          title={l("本周摘要", "This week")}
          subtitle={l("摘要只汇总，不再触发新的通知。", "A digest summarizes; it never creates extra notifications.")}
          className="mb-0"
        >
          <div className="stat-grid">
            <div className="stat">
              <span className="stat-value">{weekSessions.length}</span>
              <span className="stat-label">{l("会话", "Sessions")}</span>
            </div>
            <div className="stat">
              <span className="stat-value">{weekCandidates.length}</span>
              <span className="stat-label">{l("建议", "Suggestions")}</span>
            </div>
            <div className="stat">
              <span className="stat-value">{appliedThisWeek.length}</span>
              <span className="stat-label">{l("应用/撤销", "Applied/reverted")}</span>
            </div>
            <div className="stat">
              <span className="stat-value">{reverted.length}</span>
              <span className="stat-label">{l("总撤销", "Total reverted")}</span>
            </div>
          </div>
          <div className="chart mt-16">
            <div className="spread">
              <strong className="small">{l("最近 7 天有信号的会话", "Sessions with signals, last 7 days")}</strong>
              <span className="small">{l("绿色柱 = 有问题信号", "Green bar = signal")}</span>
            </div>
            <div className="chart-bars" role="img" aria-label={l("最近 7 天问题信号柱状图", "Bar chart of problem signals for the last 7 days")}>
              {last7.map((day) => (
                <div className="chart-col" key={day.key}>
                  <div className={`chart-bar ${barClass(day.problems, maxProblems)}`} title={`${day.key}: ${day.problems}`} />
                </div>
              ))}
            </div>
            <div className="chart-labels" aria-hidden="true">
              {last7.map((day) => (
                <span key={day.key}>{day.label}</span>
              ))}
            </div>
            <table className="sr-only">
              <caption>{l("最近 7 天问题信号", "Problem signals in the last 7 days")}</caption>
              <thead>
                <tr>
                  <th>{l("日期", "Date")}</th>
                  <th>{l("会话", "Sessions")}</th>
                  <th>{l("问题信号", "Signals")}</th>
                </tr>
              </thead>
              <tbody>
                {last7.map((day) => (
                  <tr key={day.key}>
                    <td>{day.key}</td>
                    <td>{day.sessions}</td>
                    <td>{day.problems}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      </div>

      <Section
        title={l("已应用，可撤销", "Applied and reversible")}
        subtitle={l("撤销窗口内可一键恢复；超出窗口请使用“恢复原始工作方式”。", "Revert within the undo window; after that use Restore original.")}
        actions={
          <Button size="sm" variant="ghost" onClick={() => app.setPage("history")}>
            {l("查看全部", "View all")}
          </Button>
        }
      >
        {applied.length === 0 ? (
          <p className="sub">{l("还没有应用过改动。", "No change has been applied yet.")}</p>
        ) : (
          <div className="list">
            {applied.slice(0, 3).map((candidate) => (
              <div className="list-item" key={candidate.id}>
                <div className="list-main">
                  <span className="list-title">{candidate.summary}</span>
                  <span className="sub">
                    {candidate.files_changed.join(", ")} · {timeAgo(candidate.applied_at, lang)}
                  </span>
                </div>
                <div className="list-actions">
                  <StatusMark tone={candidate.state === "reverted" ? "idle" : "ok"}>
                    {candidateStateLabel(candidate.state, lang)}
                  </StatusMark>
                  <Button size="sm" variant="ghost" icon="undo" onClick={() => setDetail(candidate.id)}>
                    {l("查看 / 撤销", "View / Revert")}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section
        title={l("最近的会话", "Recent sessions")}
        subtitle={l(
          "每个非隐身、非暂停的会话都会记录；无信号时零成本结束。",
          "Every non-incognito, non-paused session is recorded; no signal ends at zero cost.",
        )}
        actions={
          <Button size="sm" variant="ghost" onClick={() => app.setPage("history")}>
            {l("查看全部", "View all")}
          </Button>
        }
      >
        {app.sessions.length === 0 ? (
          <EmptyState icon="terminal" title={l("还没有会话记录", "No sessions recorded yet")}>
            {l(
              "照常使用 Agent。下一次会话结束后，harness2-morphd 会自动总结（隐身/暂停除外）。",
              "Keep using your agent. harness2-morphd summarizes after the next session ends, unless incognito or paused.",
            )}
          </EmptyState>
        ) : (
          <div className="list">
            {app.sessions.slice(0, 6).map((session) => (
              <div className="list-item" key={session.id}>
                <div className="list-main">
                  <span className="list-title">
                    {session.host} · {session.id}
                  </span>
                  <span className="sub">
                    {timeAgo(session.ended_at, lang)} · {l("纠正", "corrections")} {session.signals?.corrections ?? 0} ·{" "}
                    {l("工具循环", "tool loops")} {session.signals?.tool_loops ?? 0}
                  </span>
                </div>
                <div className="list-actions">
                  <StatusMark tone={statusTone(session.status)}>
                    {SESSION_STATUS[session.status]?.[lang] || session.status}
                  </StatusMark>
                  {["noop", "paused", "failed", "disabled", "project_disabled", "sensitive_project"].includes(session.status) ? (
                    <Button size="sm" variant="ghost" onClick={() => void analyze(session)}>
                      {l("立即分析", "Analyze now")}
                    </Button>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      {detail ? (
        <CandidateDetailModal
          candidateId={detail}
          onClose={() => setDetail(null)}
          onChanged={() => app.refreshData()}
        />
      ) : null}

      {confirmApply ? (
        <ConfirmDialog
          title={l("应用这条建议？", "Apply this suggestion?")}
          confirmLabel={l("应用并创建快照", "Apply and snapshot")}
          onConfirm={apply}
          onClose={() => setConfirmApply(null)}
          busy={busy}
        >
          <p className="strong">{confirmApply.summary}</p>
          <p>{confirmApply.expected_effect}</p>
          <p className="sub">{confirmApply.risk}</p>
          <InfoNote icon="undo">
            {l(
              "应用前会自动快照；可在历史中一键撤销。若目标文件被你手动改过，harness2-morphd 会停止而不是覆盖。",
              "A snapshot is created first; revert from History. If you edited a target file, harness2-morphd stops instead of overwriting.",
            )}
          </InfoNote>
        </ConfirmDialog>
      ) : null}

      {manual ? (
        <Modal
          title={l("手动分析这个会话？", "Analyze this session now?")}
          subtitle={l("会先把脱敏内容发送给你选择的模型服务商。", "Redacted content will be sent to your selected provider.")}
          onClose={() => setManual(null)}
          footer={
            <>
              <Button variant="ghost" onClick={() => setManual(null)} disabled={busy}>
                {l("取消", "Cancel")}
              </Button>
              <Button onClick={confirmAnalyze} loading={busy} icon="play">
                {l("确认分析", "Start analysis")}
              </Button>
            </>
          }
        >
          <KeyValue label={l("会话", "Session")}>{manual.session.id}</KeyValue>
          <KeyValue label={l("预计发送文件", "Files to send")}>
            {String((manual.preview as { files?: Record<string, unknown> }).files
              ? Object.keys((manual.preview as { files: Record<string, unknown> }).files).length
              : 0)}
          </KeyValue>
          <KeyValue label={l("预计 tokens", "Estimated tokens")}>
            {String((manual.preview as { estimated_tokens?: number }).estimated_tokens || "—")}
          </KeyValue>
          <KeyValue label={l("排除项", "Excluded")}>
            {String((manual.preview as { excluded?: string[] }).excluded?.length || 0)}
          </KeyValue>
          <KeyValue label={l("预计成本", "Estimated cost")}>
            {Number.isFinite(manual.estimated) ? formatMoney(manual.estimated, lang) : l("按预算预检", "Budget pre-check")}
          </KeyValue>
          <InfoNote icon="shield">
            {l(
              "只发送生成建议所需的脱敏内容；不含判据、凭据和排除目录。实际成本以服务商账单为准。",
              "Only redacted content needed for the suggestion is sent. Criteria, credentials and excluded folders are never sent. The provider bill is authoritative.",
            )}
          </InfoNote>
        </Modal>
      ) : null}

      {whyOpen ? (
        <Modal
          title={l("为什么没有建议？", "Why is there no suggestion?")}
          onClose={() => setWhyOpen(false)}
          footer={
            <Button variant="ghost" onClick={() => setWhyOpen(false)}>
              {l("知道了", "Got it")}
            </Button>
          }
        >
          {why().length === 0 ? (
            <p>{l("当前没有阻塞项；下一次会话结束后会自动判断。", "Nothing is blocking; the next session will be triaged automatically.")}</p>
          ) : (
            <ul className="step-list">
              {why().map((item) => (
                <li key={item}>
                  <span>•</span>
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          )}
          <InfoNote icon="spark">
            {l(
              "没有建议也是正常工作状态。harness2-morphd 默认每周最多 2 条主动建议，其余进入待看。",
              "No suggestion is a normal state. harness2-morphd shows at most 2 proactive suggestions a week; the rest stay in the inbox.",
            )}
          </InfoNote>
        </Modal>
      ) : null}
    </>
  );
}
