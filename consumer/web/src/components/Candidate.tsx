import { useEffect, useState } from "react";
import { api } from "../api";
import { formatMoney, timeAgo, useL, useLang } from "../i18n";
import { useApp } from "../state";
import type { Candidate } from "../types";
import { Icon } from "./Icon";
import {
  Badge,
  Button,
  CodeBlock,
  ErrorBanner,
  InfoNote,
  KeyValue,
  Modal,
  Section,
} from "./UI";

const KIND_LABELS: Record<string, { zh: string; en: string }> = {
  instruction: { zh: "说明书", en: "Instructions" },
  skill: { zh: "技能", en: "Skill" },
  script: { zh: "脚本", en: "Script" },
  agent: { zh: "子 Agent", en: "Sub-agent" },
  other: { zh: "其他", en: "Other" },
};

const PRESENT_LABELS: Record<string, { zh: string; en: string }> = {
  high_confidence: { zh: "高置信度，立即展示", en: "High confidence, shown immediately" },
  immediate: { zh: "按即时策略展示", en: "Shown immediately" },
  quiet_hours: { zh: "静默时段，进入待看", en: "Quiet hours, moved to inbox" },
  weekly_cap: { zh: "达到每周上限，进入待看", en: "Weekly cap reached, moved to inbox" },
  digest: { zh: "进入摘要，未主动展示", en: "Queued for digest" },
  focus: { zh: "专注模式，进入待看", en: "Focus mode, moved to inbox" },
  paused: { zh: "学习已暂停", en: "Learning paused" },
  incognito: { zh: "隐身", en: "Incognito" },
  downranked: { zh: "负反馈后降频", en: "Down-ranked after feedback" },
  inbox_only: { zh: "仅进入待看", en: "Inbox only" },
  kind_suppressed: { zh: "已静音此类", en: "This kind is muted" },
  feedback_quiet: { zh: "反馈后静音", en: "Quiet after feedback" },
  disabled: { zh: "自动学习已关闭", en: "Learning disabled" },
};

const STATE_COPY: Record<string, { zh: string; en: string }> = {
  needs_apply: { zh: "待看", en: "Inbox" },
  applied: { zh: "已应用", en: "Applied" },
  trial: { zh: "试用中", en: "Trial" },
  kept: { zh: "已保留", en: "Kept" },
  reverted: { zh: "已撤销", en: "Reverted" },
};

export function candidateStateLabel(state: string, lang: "zh" | "en"): string {
  const item = STATE_COPY[state];
  return item ? item[lang] : state;
}

export function CandidateCard({
  candidate,
  onOpen,
  onApply,
  onIgnore,
  compact = false,
}: {
  candidate: Candidate;
  onOpen: () => void;
  onApply: () => void;
  onIgnore: () => void;
  compact?: boolean;
}) {
  const app = useApp();
  const l = useL();
  const lang = useLang();
  const detailed = app.settings?.ui.style === "detailed";
  const applied = ["applied", "trial", "kept"].includes(candidate.state);
  return (
    <article className="candidate-card">
      <div className="candidate-top">
        <div className="stack-sm grow">
          <div className="candidate-meta">
            <Badge variant="accent">{candidateStateLabel(candidate.state, lang)}</Badge>
            <Badge>{KIND_LABELS[candidate.kind]?.[lang] || candidate.kind}</Badge>
            {candidate.confidence === "high" ? <Badge variant="inverse">{l("高置信度", "High confidence")}</Badge> : null}
            {candidate.fallback_used ? <Badge variant="dashed">{l("已使用备用模型", "Fallback used")}</Badge> : null}
            {!candidate.presented ? <Badge variant="dashed">{l("待看", "Inbox")}</Badge> : null}
          </div>
          <h3 className="candidate-summary">{candidate.summary}</h3>
          <p className="sub">
            {l("预计影响", "Expected effect")}: {candidate.expected_effect}
          </p>
          {!compact || detailed ? <p className="sub">{candidate.risk}</p> : null}
          {detailed ? (
            <p className="sub">
              {l("来源会话", "Source")}: {candidate.source?.session_id || "—"} ·{" "}
              {l("纠正", "Corrections")} {candidate.source?.signals?.corrections ?? 0} ·{" "}
              {l("工具循环", "Tool loops")} {candidate.source?.signals?.tool_loops ?? 0}
            </p>
          ) : null}
        </div>
        <div className="text-right stack-sm">
          <strong>{formatMoney(candidate.cost_usd, lang)}</strong>
          <span className="small">{timeAgo(candidate.created_at, lang)}</span>
        </div>
      </div>
      <div className="candidate-facts">
        <div className="fact">
          <strong>{l("来源", "Source")}</strong>
          {candidate.source?.session_id || "—"}
        </div>
        <div className="fact">
          <strong>{l("文件", "Files")}</strong>
          {candidate.files_changed.join(", ") || "—"}
        </div>
        <div className="fact">
          <strong>{l("试用", "Trial")}</strong>
          {candidate.trial?.n ?? 0} {l("次会话", "sessions")}
        </div>
        {candidate.automatic ? (
          <div className="fact">
            <strong>{l("方式", "Mode")}</strong>
            {l("自动应用", "Automatic")}
          </div>
        ) : null}
      </div>
      <div className="candidate-actions">
        <Button size="sm" variant="ghost" icon="eye" onClick={onOpen}>
          {l("查看改动与原因", "View change and reason")}
        </Button>
        {candidate.state === "needs_apply" ? (
          <>
            <Button size="sm" icon="check" onClick={onApply}>
              {l("应用", "Apply")}
            </Button>
            <Button size="sm" variant="ghost" icon="x" onClick={onIgnore}>
              {l("忽略", "Ignore")}
            </Button>
          </>
        ) : null}
        {applied ? <span className="badge badge-outline">{l("可在历史中撤销", "Revert in History")}</span> : null}
      </div>
    </article>
  );
}

export function CandidateDetailModal({
  candidateId,
  onClose,
  onChanged,
}: {
  candidateId: string;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const app = useApp();
  const l = useL();
  const lang = useLang();
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<"" | "apply" | "revert">("");
  const beginner = app.settings?.ui.beginner !== false;

  const load = async () => {
    try {
      setCandidate(await api.candidate(candidateId));
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candidateId]);

  const action = async (kind: "apply" | "revert" | "feedback", reason?: string) => {
    setBusy(true);
    setError(null);
    try {
      if (kind === "apply") await api.applyCandidate(candidateId);
      if (kind === "revert") await api.revertCandidate(candidateId);
      if (kind === "feedback" && reason) await api.feedback(candidateId, reason);
      await load();
      await onChanged();
      app.toast(
        "ok",
        kind === "apply" ? "建议已应用，可随时撤销" : kind === "revert" ? "已恢复到应用前" : "反馈已记录",
        kind === "apply" ? "Applied; you can revert anytime" : kind === "revert" ? "Restored the previous state" : "Feedback recorded",
      );
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
      setPending("");
    }
  };

  const feedback = [
    { reason: "useful", zh: "有用", en: "Useful" },
    { reason: "not_useful", zh: "没用", en: "Not useful" },
    { reason: "intrusive", zh: "太侵入", en: "Too intrusive" },
    { reason: "expensive", zh: "太贵", en: "Too expensive" },
    { reason: "wrong", zh: "不对", en: "Wrong" },
    { reason: "already_known", zh: "我已经知道了", en: "Already knew" },
    { reason: "not_this_kind", zh: "不再建议此类", en: "Not this kind" },
  ];

  return (
    <Modal
      title={candidate?.summary || l("改进建议", "Improvement suggestion")}
      subtitle={
        candidate
          ? `${candidateStateLabel(candidate.state, lang)} · ${candidate.host} · ${formatMoney(candidate.cost_usd, lang)}`
          : undefined
      }
      onClose={onClose}
      wide
      footer={
        candidate ? (
          <>
            {candidate.state === "needs_apply" ? (
              pending === "apply" ? (
                <>
                  <span className="small">
                    {l("确认应用？应用前会创建快照。", "Apply now? A snapshot will be created first.")}
                  </span>
                  <Button icon="check" onClick={() => void action("apply")} loading={busy}>
                    {l("确认应用", "Confirm apply")}
                  </Button>
                  <Button variant="ghost" onClick={() => setPending("")} disabled={busy}>
                    {l("取消", "Cancel")}
                  </Button>
                </>
              ) : (
                <Button icon="check" onClick={() => setPending("apply")} loading={busy}>
                  {l("应用改进", "Apply improvement")}
                </Button>
              )
            ) : null}
            {["applied", "trial", "kept"].includes(candidate.state) ? (
              pending === "revert" ? (
                <>
                  <span className="small">{l("确认恢复到应用前？", "Restore the pre-apply state?")}</span>
                  <Button variant="danger" onClick={() => void action("revert")} loading={busy}>
                    {l("确认撤销", "Confirm revert")}
                  </Button>
                  <Button variant="ghost" onClick={() => setPending("")} disabled={busy}>
                    {l("取消", "Cancel")}
                  </Button>
                </>
              ) : (
                <Button variant="danger" icon="undo" onClick={() => setPending("revert")} disabled={busy}>
                  {l("撤销", "Revert")}
                </Button>
              )
            ) : null}
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              {l("关闭", "Close")}
            </Button>
          </>
        ) : null
      }
    >
      {error ? <ErrorBanner error={app.handleError(error)} onRetry={load} /> : null}
      {!candidate ? (
        <div className="skeleton-stack">
          <div className="skeleton" />
          <div className="skeleton" />
          <div className="skeleton" />
        </div>
      ) : (
        <>
          <div className="grid-2">
            <div className="stack-sm">
              <KeyValue label={l("预计影响", "Expected effect")}>{candidate.expected_effect}</KeyValue>
              <KeyValue label={l("风险", "Risk")}>{candidate.risk}</KeyValue>
              <KeyValue label={l("文件", "Files")}>{candidate.files_changed.join(", ")}</KeyValue>
              {!beginner ? (
                <KeyValue label={l("基线", "Base")}>
                  <span className="mono small">{candidate.base_hash.slice(0, 16)}…</span>
                </KeyValue>
              ) : null}
              <KeyValue label={l("成本（估算）", "Cost (estimate)")}>{formatMoney(candidate.cost_usd, lang)}</KeyValue>
              {candidate.model_used || candidate.model ? (
                <KeyValue label={l("分析模型", "Analysis model")}>
                  <span className="mono small wrap-anywhere">
                    {candidate.model_used || candidate.model}
                  </span>
                </KeyValue>
              ) : null}
            </div>
            <div className="stack-sm">
              <KeyValue label={l("来自会话", "Source session")}>{candidate.source?.session_id || "—"}</KeyValue>
              <KeyValue label={l("会话结束", "Session ended")}>{timeAgo(candidate.source?.ended_at, lang)}</KeyValue>
              <KeyValue label={l("会话信号", "Signals")}>
                {l("纠正", "Corrections")} {candidate.source?.signals?.corrections ?? 0} ·{" "}
                {l("工具循环", "Tool loops")} {candidate.source?.signals?.tool_loops ?? 0}
              </KeyValue>
              <KeyValue label={l("安全检查", "Safety")}>
                {candidate.safety?.ok ? l("通过", "Passed") : l("未通过", "Failed")}
                {candidate.safety?.violations?.length ? (
                  <ul className="step-list">
                    {candidate.safety.violations.map((violation) => (
                      <li key={violation}><span>•</span><span>{violation}</span></li>
                    ))}
                  </ul>
                ) : null}
              </KeyValue>
              <KeyValue label={l("展示原因", "Presentation")}>{PRESENT_LABELS[candidate.presentation_reason]?.[lang] || candidate.presentation_reason || "—"}</KeyValue>
              {candidate.fallback_used ? (
                <KeyValue label={l("模型降级", "Fallback")}>
                  {l("主模型不可用，已按顺序使用备用模型", "Primary unavailable; a fallback model was used")}
                </KeyValue>
              ) : null}
            </div>
          </div>
          <InfoNote icon="shield">
            {l(
              "应用前会自动快照；不会修改权限、沙箱、hooks、settings 或 MCP。撤销会恢复快照，并检测你是否手动改过文件。",
              "A snapshot is taken before applying. Permissions, sandbox, hooks, settings and MCP are never changed. Revert restores the snapshot and detects manual edits.",
            )}
          </InfoNote>
          {candidate.trial?.n ? (
            <Section title={l("试用观察", "Trial observation")} subtitle={l("仅统计非隐身会话。", "Only non-incognito sessions count.")}>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{l("会话", "Session")}</th>
                      <th>{l("达成", "Achieved")}</th>
                      <th>{l("纠正", "Corrections")}</th>
                      <th>{l("工具循环", "Tool loops")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {candidate.trial.observed.map((item) => (
                      <tr key={item.session_id}>
                        <td>{item.session_id}</td>
                        <td>{String(item.signals?.achieved)}</td>
                        <td>{item.signals?.corrections ?? 0}</td>
                        <td>{item.signals?.tool_loops ?? 0}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Section>
          ) : null}
          <CodeBlock code={candidate.diff || ""} label={l("文件改动对比", "File change")} />
          <div className="stack-sm">
            <strong>{l("这条建议有用吗？", "Was this suggestion useful?")}</strong>
            <div className="row">
              {feedback.map((item) => (
                <Button
                  key={item.reason}
                  size="sm"
                  variant="ghost"
                  onClick={() => void action("feedback", item.reason)}
                  disabled={busy}
                >
                  {lang === "zh" ? item.zh : item.en}
                </Button>
              ))}
            </div>
            <p className="sub">
              <Icon name="alert" size={14} />{" "}
              {l(
                "“没用/不对”会立即撤销已应用的改动；连续忽略或负反馈会让 harness2-morphd 自动降频。",
                "Not useful / wrong reverts an applied change immediately; repeated ignores or negative feedback reduce frequency automatically.",
              )}
            </p>
          </div>
        </>
      )}
    </Modal>
  );
}
