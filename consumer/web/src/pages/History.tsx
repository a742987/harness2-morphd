import { useMemo, useState } from "react";
import { api } from "../api";
import { CandidateCard, CandidateDetailModal } from "../components/Candidate";
import {
  Badge,
  Button,
  EmptyState,
  ErrorBanner,
  Modal,
  PageHeader,
  Section,
  StatusMark,
  Tabs,
} from "../components/UI";
import { formatMoney, formatDate, timeAgo, useL, useLang } from "../i18n";
import { useApp } from "../state";
import type { Candidate, SessionRecord } from "../types";

const STATUS_COPY: Record<string, { zh: string; en: string }> = {
  recorded: { zh: "已记录", en: "Recorded" },
  queued: { zh: "已入队", en: "Queued" },
  analyzing: { zh: "分析中", en: "Analyzing" },
  analyzed: { zh: "已分析", en: "Analyzed" },
  applied: { zh: "已应用", en: "Applied" },
  noop: { zh: "无需调整", en: "No change" },
  paused: { zh: "已暂停", en: "Paused" },
  disabled: { zh: "学习已关", en: "Learning off" },
  project_disabled: { zh: "项目已禁用", en: "Project disabled" },
  sensitive_project: { zh: "敏感项目", en: "Sensitive" },
  needs_confirm: { zh: "等待确认", en: "Needs confirm" },
  failed: { zh: "失败", en: "Failed" },
  canceled: { zh: "已取消", en: "Canceled" },
};

type Tab = "inbox" | "candidates" | "sessions" | "jobs";

function sessionTone(status: string): "ok" | "warn" | "idle" | "bad" {
  if (["analyzed", "applied", "noop"].includes(status)) return "ok";
  if (["queued", "analyzing", "needs_confirm"].includes(status)) return "warn";
  if (status === "failed") return "bad";
  return "idle";
}

export function History() {
  const app = useApp();
  const l = useL();
  const lang = useLang();
  const [tab, setTab] = useState<Tab>("inbox");
  const [detail, setDetail] = useState<string | null>(null);
  const [project, setProject] = useState<string>("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [sessionDetail, setSessionDetail] = useState<SessionRecord | null>(null);

  const inbox = app.candidates.filter(
    (candidate) => candidate.state === "needs_apply" && !candidate.feedback?.some((item) => item.reason === "ignore"),
  );
  const candidates = useMemo(
    () => (project ? app.candidates.filter((candidate) => candidate.project_id === project) : app.candidates),
    [app.candidates, project],
  );
  const sessions = useMemo(
    () => (project ? app.sessions.filter((session) => session.project_id === project) : app.sessions),
    [app.sessions, project],
  );
  const jobs = useMemo(
    () => (project ? app.jobs.filter((job) => job.project_id === project) : app.jobs),
    [app.jobs, project],
  );

  const ignore = async (candidate: Candidate) => {
    try {
      await api.feedback(candidate.id, "ignore");
      await app.refreshData();
      app.toast("info", "已移入待看；连续忽略会自动降频", "Moved to inbox; repeated ignores reduce frequency");
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    }
  };
  const analyze = async (session: SessionRecord) => {
    try {
      const result = await api.analyzeSession(session.id, true);
      await app.refreshData();
      app.toast("ok", `已加入分析队列${result.job_id ? `（${result.job_id}）` : ""}`, "Added to the analysis queue");
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    }
  };
  const cancel = async (jobId: string) => {
    setBusy(true);
    try {
      await api.cancelJob(jobId);
      await app.refreshData();
      app.toast("ok", "任务已取消", "Job canceled");
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title={l("历史与待看", "History and inbox")}
        subtitle={l(
          "每条建议都保留来源会话、信号、成本、安全结果和 diff；应用后可一键撤销。",
          "Every suggestion keeps its source session, signals, cost, safety result, and diff. Applied changes can be reverted.",
        )}
        actions={
          <select
            className="input select"
            aria-label={l("按项目筛选", "Filter by project")}
            value={project}
            onChange={(event) => setProject(event.target.value)}
          >
            <option value="">{l("所有项目", "All projects")}</option>
            {app.projects.map((item) => (
              <option key={item.id} value={item.id}>
                {item.domain} · {item.id}
              </option>
            ))}
          </select>
        }
      />
      {error ? <ErrorBanner error={app.handleError(error)} /> : null}
      <Tabs
        value={tab}
        onChange={setTab}
        label={l("历史分类", "History sections")}
        tabs={[
          { value: "inbox", label: l("待看", "Inbox"), count: inbox.length },
          { value: "candidates", label: l("全部建议", "All suggestions"), count: candidates.length },
          { value: "sessions", label: l("会话", "Sessions"), count: sessions.length },
          { value: "jobs", label: l("任务", "Jobs"), count: jobs.length },
        ]}
      />

      {tab === "inbox" ? (
        <Section
          title={l("等你处理的建议", "Waiting for you")}
          subtitle={l("应用前会自动快照；忽略后进入待看并降低后续频率。", "Applying creates a snapshot; ignoring moves it to the inbox and reduces frequency.")}
        >
          {inbox.length === 0 ? (
            <EmptyState icon="spark" title={l("待看为空", "Inbox empty")}>
              {l(
                "morphd 不会为了凑数而生成建议。没有信号时会零成本跳过。",
                "morphd does not generate suggestions just to fill space. No signal means a zero-cost skip.",
              )}
            </EmptyState>
          ) : (
            <div className="stack">
              {inbox.map((candidate) => (
                <CandidateCard
                  key={candidate.id}
                  candidate={candidate}
                  onOpen={() => setDetail(candidate.id)}
                  onApply={() => setDetail(candidate.id)}
                  onIgnore={() => void ignore(candidate)}
                />
              ))}
            </div>
          )}
        </Section>
      ) : null}

      {tab === "candidates" ? (
        <Section
          title={l("全部建议", "All suggestions")}
          subtitle={l("包含待看、已应用、试用、保留与已撤销。", "Inbox, applied, trial, kept, and reverted.")}
        >
          {candidates.length === 0 ? (
            <EmptyState icon="history" title={l("还没有建议记录", "No suggestions yet")} />
          ) : (
            <div className="stack">
              {candidates.map((candidate) => (
                <CandidateCard
                  key={candidate.id}
                  candidate={candidate}
                  onOpen={() => setDetail(candidate.id)}
                  onApply={() => setDetail(candidate.id)}
                  onIgnore={() => void ignore(candidate)}
                />
              ))}
            </div>
          )}
        </Section>
      ) : null}

      {tab === "sessions" ? (
        <Section
          title={l("会话记录", "Session records")}
          subtitle={l("隐身会话不会出现在这里；暂停/禁用期间只保留最小状态。", "Incognito sessions never appear here; paused/disabled periods keep only minimal state.")}
        >
          {sessions.length === 0 ? (
            <EmptyState icon="terminal" title={l("还没有会话记录", "No sessions yet")} />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{l("会话", "Session")}</th>
                    <th>{l("时间", "Time")}</th>
                    <th>{l("信号", "Signals")}</th>
                    <th>{l("状态", "Status")}</th>
                    <th>{l("操作", "Actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((session) => (
                    <tr key={session.id}>
                      <td>
                        <strong>{session.host}</strong>
                        <div className="mono small wrap-anywhere">{session.id}</div>
                        <div className="small wrap-anywhere">{session.project}</div>
                      </td>
                      <td className="small">{timeAgo(session.ended_at, lang)}</td>
                      <td className="small">
                        {l("纠正", "Corrections")} {session.signals?.corrections ?? 0}
                        <br />
                        {l("工具循环", "Loops")} {session.signals?.tool_loops ?? 0}
                        <br />
                        {l("达成", "Achieved")} {String(session.signals?.achieved)}
                      </td>
                      <td>
                        <StatusMark tone={sessionTone(session.status)}>
                          {STATUS_COPY[session.status]?.[lang] || session.status}
                        </StatusMark>
                      </td>
                      <td className="actions">
                        <div className="row">
                          <Button size="sm" variant="ghost" icon="eye" onClick={() => setSessionDetail(session)}>
                            {l("详情", "Details")}
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            icon="play"
                            onClick={() => void analyze(session)}
                            disabled={["analyzing", "analyzed", "applied", "needs_confirm"].includes(session.status)}
                          >
                            {l("分析", "Analyze")}
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      ) : null}

      {tab === "jobs" ? (
        <Section
          title={l("分析任务", "Analysis jobs")}
          subtitle={l("默认并发 1；宿主退出会写检查点并暂停，下次启动继续。", "Concurrency 1; host exit checkpoints and pauses, resuming next launch.")}
        >
          {jobs.length === 0 ? (
            <EmptyState icon="diagnostics" title={l("还没有分析任务", "No analysis jobs")} />
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{l("任务", "Job")}</th>
                    <th>{l("状态", "State")}</th>
                    <th>{l("成本", "Cost")}</th>
                    <th>{l("原因/错误", "Reason/error")}</th>
                    <th>{l("操作", "Actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map((job) => (
                    <tr key={job.id}>
                      <td>
                        <strong>{job.id}</strong>
                        <div className="small">{job.session_id}</div>
                        <div className="small">{formatDate(job.created_at, lang)}</div>
                      </td>
                      <td>
                        <Badge variant={job.state === "failed" ? "dashed" : job.state === "applied" ? "accent" : "outline"}>
                          {job.state}
                        </Badge>
                      </td>
                      <td>{formatMoney(job.cost_usd || 0, lang)}</td>
                      <td className="small wrap-anywhere">
                        {job.reason || "—"}
                        {job.error?.error ? (
                          <div>
                            {job.error.error.code}: {job.error.error.message}
                          </div>
                        ) : null}
                      </td>
                      <td className="actions">
                        {["queued", "running", "paused"].includes(job.state) ? (
                          <Button size="sm" variant="ghost" icon="x" onClick={() => void cancel(job.id)} loading={busy}>
                            {l("取消", "Cancel")}
                          </Button>
                        ) : (
                          <span className="small">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Section>
      ) : null}

      {detail ? (
        <CandidateDetailModal
          candidateId={detail}
          onClose={() => setDetail(null)}
          onChanged={() => app.refreshData()}
        />
      ) : null}

      {sessionDetail ? (
        <SessionDetails session={sessionDetail} onClose={() => setSessionDetail(null)} />
      ) : null}
    </>
  );
}

function SessionDetails({ session, onClose }: { session: SessionRecord; onClose: () => void }) {
  const l = useL();
  const lang = useLang();
  return (
    <Modal
      title={session.id}
      subtitle={`${session.host} · ${session.project}`}
      onClose={onClose}
      footer={
        <Button variant="ghost" onClick={onClose}>
          {l("关闭", "Close")}
        </Button>
      }
    >
          <div className="grid-2">
            <div className="fact">
              <strong>{l("状态", "Status")}</strong>
              {STATUS_COPY[session.status]?.[lang] || session.status}
            </div>
            <div className="fact">
              <strong>{l("结束原因", "End reason")}</strong>
              {session.end_reason}
            </div>
            <div className="fact">
              <strong>{l("达成", "Achieved")}</strong>
              {String(session.signals?.achieved)}
            </div>
            <div className="fact">
              <strong>{l("纠正/循环", "Correct/loops")}</strong>
              {session.signals?.corrections ?? 0} / {session.signals?.tool_loops ?? 0}
            </div>
            <div className="fact">
              <strong>{l("脱敏", "Redaction")}</strong>
              {session.redaction?.redacted_fields?.length || 0}
            </div>
            <div className="fact">
              <strong>{l("结束时间", "Ended")}</strong>
              {formatDate(session.ended_at, lang)}
            </div>
          </div>
          <div className="stack-sm">
            <strong>{l("本地摘要", "Local summary")}</strong>
            <pre className="code-block">{session.summary || l("（无摘要）", "(no summary)")}</pre>
          </div>
          {session.error ? (
            <ErrorBanner
              error={{
                what: l("这次会话的分析失败了。", "Analysis of this session failed."),
                why: `${session.error.code}: ${session.error.message}`,
                action: l("查看诊断页，或重试分析。", "Open Diagnostics or retry."),
                money: l("失败前是否产生费用以预算页为准。", "Check the Budget page for any cost before failure."),
              }}
            />
          ) : null}
    </Modal>
  );
}
