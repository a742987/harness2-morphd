import { useState } from "react";
import {
  Badge,
  Button,
  ErrorBanner,
  Field,
  InfoNote,
  Input,
  PageHeader,
  Progress,
  Section,
  Toggle,
} from "../components/UI";
import { formatMoney, timeAgo, useL, useLang } from "../i18n";
import { useApp } from "../state";

export function Budget() {
  const app = useApp();
  const l = useL();
  const lang = useLang();
  const settings = app.settings;
  const budget = app.budget;
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (!settings) return null;

  const save = async (patch: Partial<typeof settings.budget>) => {
    setError(null);
    setBusy(true);
    const saved = await app.saveSettings({ budget: { ...settings.budget, ...patch } });
    setBusy(false);
    if (saved) await app.refreshAll(true);
  };

  const costed = app.candidates.filter((candidate) => Number(candidate.cost_usd) > 0);
  const recent = costed.slice(0, 8);
  const lastSpend = recent[0]?.cost_usd || 0;
  const analysisCount = app.jobs.filter((job) => Number(job.cost_usd) > 0).length || costed.length;

  return (
    <>
      <PageHeader
        title={l("预算与花费", "Budget and spend")}
        subtitle={l(
          "三层硬上限：月度、单会话、单次分析。harness2-morphd 在模型调用前做最坏情况预检，超限就不启动。",
          "Three hard limits: monthly, per session, per analysis. harness2-morphd pre-checks the worst case before any model call and never starts when over budget.",
        )}
      />
      {error ? <ErrorBanner error={app.handleError(error)} /> : null}

      <div className="grid-2 mb-24">
        <div className="card card-accent">
          <div className="spread">
            <strong>{l("本月已花（估算）", "Spent this month (estimate)")}</strong>
            <Badge variant="inverse">{l("以账单为准", "Bill is authoritative")}</Badge>
          </div>
          <div className="stat-value">{formatMoney(budget?.monthly.spent || 0, lang)}</div>
          <Progress value={budget?.monthly.spent || 0} max={budget?.monthly.limit || 1} label={l("月度预算已用", "Monthly budget used")} />
          <div className="spread mt-8">
            <span className="small">{l("剩余", "Remaining")} {formatMoney(budget?.monthly.remaining || 0, lang)}</span>
            <span className="small">{l("上限", "Limit")} {formatMoney(budget?.monthly.limit || 0, lang)}</span>
          </div>
        </div>
        <div className="card">
          <div className="spread">
            <strong>{l("状态", "Status")}</strong>
            <Badge variant={budget?.blocked ? "dashed" : budget?.alert ? "inverse" : "accent"}>
              {budget?.blocked ? l("已停止自动分析", "Automatic analysis stopped") : budget?.alert ? l("达到提醒阈值", "Alert threshold reached") : l("正常", "Normal")}
            </Badge>
          </div>
          <p className="sub mt-8">
            {budget?.blocked
              ? l(
                  "本月自动学习预算已用完。本月不再自动分析；你可以调高预算或等下月。",
                  "This month's budget is exhausted. No more automatic analysis this month; raise the limit or wait.",
                )
              : budget?.alert
                ? l(
                    "已达到提醒阈值。剩余预算仅供继续使用，不会被主动花完。",
                    "The alert threshold is reached. Remaining budget is available but never spent just to use it up.",
                  )
                : l(
                    "所有分析都会先做预算预检；达到上限后在下一次调用前停止。",
                    "Every analysis is pre-checked; after a limit is reached, no next call is made.",
                  )}
          </p>
          <div className="row mt-8">
            <Badge>{l("提醒阈值", "Alert")} {Math.round((settings.budget.alert_ratio || 0.8) * 100)}%</Badge>
            <Badge>{l("分析次数", "Analyses")} {analysisCount}</Badge>
            <Badge>{l("最近一次", "Last")} {formatMoney(lastSpend, lang)}</Badge>
          </div>
        </div>
      </div>

      <Section
        title={l("三层上限", "Three limits")}
        subtitle={l("修改会立即生效；进行中的调用不会被追扣，但下一次调用前会检查。", "Changes apply immediately; in-flight calls are not re-priced, but the next call is checked.")}
      >
        <div className="grid-3">
          <Field label={l("月度上限（美元）", "Monthly limit (USD)")} hint={l("硬上限；达到即停止自动分析。", "Hard limit; automatic analysis stops when reached.")} htmlFor="budget-monthly">
            <Input
              id="budget-monthly"
              type="number"
              min={0}
              step="0.5"
              value={settings.budget.monthly_usd}
              onChange={(event) => void save({ monthly_usd: Math.max(0, Number(event.target.value) || 0) })}
              disabled={busy}
            />
          </Field>
          <Field label={l("单会话上限（美元）", "Per-session limit (USD)")} hint={l("超过则不分析该会话。", "An over-budget session is not analyzed.")} htmlFor="budget-session">
            <Input
              id="budget-session"
              type="number"
              min={0}
              step="0.1"
              value={settings.budget.per_session_usd}
              onChange={(event) => void save({ per_session_usd: Math.max(0, Number(event.target.value) || 0) })}
              disabled={busy}
            />
          </Field>
          <Field label={l("单次分析上限（美元）", "Per-analysis limit (USD)")} hint={l("超过则不启动模型调用。", "No model call starts when over budget.")} htmlFor="budget-job">
            <Input
              id="budget-job"
              type="number"
              min={0}
              step="0.1"
              value={settings.budget.per_job_usd}
              onChange={(event) => void save({ per_job_usd: Math.max(0, Number(event.target.value) || 0) })}
              disabled={busy}
            />
          </Field>
        </div>
        <div className="grid-2 mt-16">
          <Field label={l("预算提醒（0–100%）", "Budget alert (0–100%)")} hint={l("达到比例时在应用内提醒一次；不会发送系统通知。", "One in-app alert at this ratio; no system notification.")} htmlFor="budget-alert">
            <Input
              id="budget-alert"
              type="number"
              min={0}
              max={100}
              step={1}
              value={Math.round((settings.budget.alert_ratio || 0) * 100)}
              onChange={(event) =>
                void save({
                  alert_ratio: Math.max(0, Math.min(100, Number(event.target.value) || 0)) / 100,
                })
              }
              disabled={busy}
            />
          </Field>
          <Toggle
            checked={settings.budget.confirm_before_analysis}
            onChange={(next) => void save({ confirm_before_analysis: next })}
            label={l("每次分析前多一次确认", "Ask before every analysis")}
            hint={l("适合极度谨慎用户；会让自动触发多一次点击。", "For very cautious users; adds one click to automatic triggers.")}
          />
        </div>
      </Section>

      <Section
        title={l("按类型拆分", "Spend by kind")}
        subtitle={l("本月分析调用汇总；单价未知时不会显示为 $0。", "This month's analysis calls; unknown prices are never shown as $0.")}
      >
        {budget?.by_kind?.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{l("类型", "Kind")}</th>
                  <th>{l("模型/标签", "Model / label")}</th>
                  <th>{l("估算成本", "Estimated cost")}</th>
                </tr>
              </thead>
              <tbody>
                {budget.by_kind.map((item) => (
                  <tr key={`${item.kind}-${item.label}`}>
                    <td>{item.kind}</td>
                    <td className="mono small wrap-anywhere">{item.label}</td>
                    <td>{formatMoney(item.usd, lang)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="sub">{l("本月还没有产生分析费用。", "No analysis cost this month.")}</p>
        )}
        <InfoNote icon="budget">
          {l(
            "价格以服务商实际账单为准。harness2-morphd 记录估算成本与实际 usage；未知 usage 按保守估算计入，不按 0。",
            "The provider bill is authoritative. harness2-morphd records estimated cost and actual usage; unknown usage is charged at a conservative estimate, never zero.",
          )}
        </InfoNote>
      </Section>

      <Section
        title={l("最近的分析成本", "Recent analysis costs")}
        subtitle={l("每次建议都可见来源、文件、成本与安全结果。", "Every suggestion shows its source, files, cost, and safety result.")}
      >
        {recent.length === 0 ? (
          <p className="sub">{l("还没有可显示的分析成本。", "No analysis cost to show yet.")}</p>
        ) : (
          <div className="list">
            {recent.map((candidate) => (
              <div className="list-item" key={candidate.id}>
                <div className="list-main">
                  <span className="list-title">{candidate.summary}</span>
                  <span className="sub">
                    {timeAgo(candidate.created_at, lang)} · {candidate.files_changed.join(", ")}
                  </span>
                </div>
                <div className="row">
                  <span className="strong">{formatMoney(candidate.cost_usd, lang)}</span>
                  <Badge variant="outline">{candidate.state}</Badge>
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="row mt-16">
          <Button variant="ghost" icon="history" onClick={() => app.setPage("history")}>
            {l("查看全部历史", "View all history")}
          </Button>
          <Button variant="ghost" icon="model" onClick={() => app.setPage("models")}>
            {l("调整模型价格", "Adjust model prices")}
          </Button>
        </div>
      </Section>
    </>
  );
}
