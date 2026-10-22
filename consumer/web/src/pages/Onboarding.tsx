import { useMemo, useState } from "react";
import { api } from "../api";
import { useL, useLang } from "../i18n";
import { useApp } from "../state";
import type { HostCandidate, Provider } from "../types";
import { Icon } from "../components/Icon";
import { ProviderForm } from "../components/ProviderForm";
import { Button, ErrorBanner, Field, InfoNote, Input, Toggle } from "../components/UI";

const STEPS = [
  { zh: "找到宿主", en: "Find your host" },
  { zh: "接入模型", en: "Connect a model" },
  { zh: "预算与隐私", en: "Budget & privacy" },
];

const PRESET_MAP = {
  frugal: { provider: "deepseek", depth: "light", zh: "省钱", en: "Frugal" },
  balanced: { provider: "openai", depth: "adaptive", zh: "均衡", en: "Balanced" },
  quality: { provider: "anthropic", depth: "full", zh: "效果优先", en: "Quality" },
  local: { provider: "ollama", depth: "adaptive", zh: "本地", en: "Local" },
} as const;

export function Onboarding() {
  const app = useApp();
  const l = useL();
  const lang = useLang();
  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState<HostCandidate | null>(null);
  const [projectRoot, setProjectRoot] = useState("");
  const [manual, setManual] = useState(false);
  const [manualKind, setManualKind] = useState<"dsh" | "claude-code" | "codex">("dsh");
  const [installPath, setInstallPath] = useState("");
  const [configPath, setConfigPath] = useState("");
  const [providerId, setProviderId] = useState("");
  const [preset, setPreset] = useState<keyof typeof PRESET_MAP>("balanced");
  const [monthly, setMonthly] = useState(5);
  const [perSession, setPerSession] = useState(0.5);
  const [perJob, setPerJob] = useState(1);
  const [retention, setRetention] = useState(30);
  const [enabled, setEnabled] = useState(true);

  const compatible = app.hosts.filter((host) => host.compatible && !host.installed);
  const installed = app.hosts.filter((host) => host.installed);
  const providers = app.providers?.providers || [];
  const chosenProvider = useMemo(
    () => providers.find((provider) => provider.id === providerId) || providers[0],
    [providers, providerId],
  );
  const templateId = PRESET_MAP[preset].provider;
  const presetCopy = app.providers?.presets || {};
  const catalog = app.providers?.catalog || [];
  const providerLabel = (id: string) => catalog.find((item) => item.id === id)?.label || id;
  const providerModel = (id: string) => catalog.find((item) => item.id === id)?.model || "";

  const handleError = (caught: unknown) => {
    setError(caught);
    app.handleError(caught);
  };

  const discover = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.discoverHosts();
      await app.refreshData();
      app.toast("ok", "已重新检查本机 Harness", "Re-scanned local Harnesses");
    } catch (caught) {
      handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const adapt = async () => {
    setBusy(true);
    setError(null);
    try {
      if (manual) {
        if (!installPath.trim()) {
          app.toast("warn", "请填写安装路径", "Enter an install path");
          return;
        }
        await api.adaptHost({
          manual: true,
          kind: manualKind,
          install_path: installPath,
          config_path: configPath || undefined,
          project_root: projectRoot || undefined,
        });
      } else if (selected) {
        await api.adaptHost({
          candidate: selected,
          project_root: projectRoot || undefined,
        });
      } else {
        setStep(1);
        return;
      }
      await app.refreshData();
      app.toast("ok", "宿主已适配", "Host adapted");
      setStep(1);
    } catch (caught) {
      handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const providerSaved = async (provider: Provider) => {
    setProviderId(provider.id);
    const current = app.settings;
    if (current) {
      await app.saveSettings({
        models: {
          ...current.models,
          preset: preset === "local" ? "local" : preset,
          proposer: `${provider.id}/${provider.model}`,
          composer: `${provider.id}/${provider.model}`,
          selector: `${provider.id}/${provider.model}`,
        },
        analysis: { ...current.analysis, depth: PRESET_MAP[preset].depth as "light" | "adaptive" | "full" },
      });
    }
    await app.refreshData();
  };

  const chooseExisting = async () => {
    if (!chosenProvider || !app.settings) {
      setStep(2);
      return;
    }
    await app.saveSettings({
      models: {
        ...app.settings.models,
        preset: "custom",
        proposer: `${chosenProvider.id}/${chosenProvider.model}`,
        composer: `${chosenProvider.id}/${chosenProvider.model}`,
        selector: `${chosenProvider.id}/${chosenProvider.model}`,
      },
    });
    setStep(2);
  };

  const finish = async () => {
    setBusy(true);
    setError(null);
    try {
      await app.saveSettings({
        enabled,
        budget: {
          monthly_usd: monthly,
          per_session_usd: perSession,
          per_job_usd: perJob,
          alert_ratio: 0.8,
          confirm_before_analysis: false,
        },
        privacy: {
          telemetry: false,
          retention_days: retention,
          excluded_globs: app.settings?.privacy.excluded_globs || [],
          local_only: app.settings?.privacy.local_only || false,
          auto_discover_hosts: true,
        },
      });
      await app.refreshData();
      app.dismissOnboarding();
      app.setPage("home");
    } catch (caught) {
      handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="onboarding">
      <aside className="onboarding-aside">
        <div>
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">m</span>
            <span className="brand-text">
              <strong>morphd</strong>
              <span>{l("安静地变好", "quietly better")}</span>
            </span>
          </div>
          <h1 className="mt-24">
            {l("照常用 Agent。", "Keep using your agent.")}
            <br />
            {l("改进交给我。", "Leave the improvement to us.")}
          </h1>
          <p className="sub mt-16">
            {l(
              "安装一次；每次会话结束后安静总结；有改进时给你一条可理解、可预算、可撤销的建议。",
              "Install once; each session is summarized quietly; when there is a safe improvement, you get an explainable, budgeted, reversible suggestion.",
            )}
          </p>
          <div className="onboarding-steps" aria-label={l("安装步骤", "Setup steps")}>
            {STEPS.map((item, index) => (
              <div
                key={item.zh}
                className={`onboarding-step ${index === step ? "is-current" : ""}`}
                aria-current={index === step ? "step" : undefined}
              >
                <span className="badge badge-outline">{index + 1}</span>
                <span>{lang === "zh" ? item.zh : item.en}</span>
                {index < step ? <Icon name="check" size={16} /> : null}
              </div>
            ))}
          </div>
        </div>
        <div className="stack-sm">
          <strong>{l("默认就很安静", "Quiet by default")}</strong>
          <span className="sub">
            {l(
              "顾问模式 · 保守改动 · 不自动应用 · 不弹系统通知 · 每周最多 2 条主动建议。",
              "Advisor mode · conservative changes · no auto-apply · no system notifications · at most 2 proactive suggestions a week.",
            )}
          </span>
          <button type="button" className="link" onClick={() => app.dismissOnboarding()}>
            {l("跳过引导，先看看", "Skip setup for now")}
          </button>
        </div>
      </aside>
      <main className="onboarding-main">
        <div className="row spread">
          <span className="badge badge-accent">
            {l("第", "Step")} {step + 1} / 3
          </span>
          <span className="small">{l("设置随时可改", "Change any setting later")}</span>
        </div>
        {error ? <ErrorBanner error={app.handleError(error)} /> : null}

        {step === 0 ? (
          <section className="stack" aria-labelledby="onboard-host">
            <div>
              <h1 id="onboard-host">{l("找到你的 Agent 宿主", "Find your agent host")}</h1>
              <p className="sub mt-8">
                {l(
                  "morphd 只检查 PATH、npm 全局目录和常见安装/配置目录，不扫描整个磁盘，也不读取凭据。",
                  "morphd checks PATH, npm global locations and common install/config directories. It never scans the whole disk or reads credentials.",
                )}
              </p>
            </div>
            {installed.length > 0 ? (
              <InfoNote tone="ok" icon="check">
                {l(
                  `已适配：${installed.map((host) => host.label).join("、")}。可以继续下一步。`,
                  `Adapted: ${installed.map((host) => host.label).join(", ")}. Continue to the next step.`,
                )}
              </InfoNote>
            ) : null}
            {compatible.length > 0 && !manual ? (
              <div className="stack-sm" role="radiogroup" aria-label={l("发现的宿主", "Discovered hosts")}>
                {compatible.map((host) => (
                  <button
                    key={host.id}
                    type="button"
                    role="radio"
                    aria-checked={selected?.id === host.id}
                    className={`option ${selected?.id === host.id ? "is-active" : ""}`}
                    onClick={() => setSelected(host)}
                  >
                    <div className="spread">
                      <strong>{host.label} {host.version}</strong>
                      <span className="badge badge-outline">{host.integration}</span>
                    </div>
                    <p className="sub wrap-anywhere">{host.executable}</p>
                    <p className="sub">
                      {l("配置目录：", "Config: ")}
                      {host.config_dir || l("未找到", "not found")}
                    </p>
                  </button>
                ))}
              </div>
            ) : (
              <InfoNote tone="warn" icon="alert">
                {l(
                  "没有自动找到可适配的宿主。你可以手动选择安装目录/可执行文件；错误目录不会写入任何文件。",
                  "No adaptable host was found automatically. Choose the install folder or executable manually; a wrong folder is never written to.",
                )}
              </InfoNote>
            )}
            {!manual ? (
              <div className="stack-sm">
                <Field
                  label={l("项目文件夹（推荐）", "Project folder (recommended)")}
                  hint={l(
                    "改进建议只会作用于这个项目的说明/技能/脚本；留空则使用宿主的默认配置目录。",
                    "Suggestions only touch this project's instructions/skills/scripts. Leave blank to use the host config directory.",
                  )}
                  htmlFor="onboard-project"
                >
                  <Input
                    id="onboard-project"
                    value={projectRoot}
                    onChange={(event) => setProjectRoot(event.target.value)}
                    placeholder="/path/to/project"
                    spellCheck={false}
                  />
                </Field>
                <div className="row">
                  <Button
                    variant="ghost"
                    icon="refresh"
                    onClick={discover}
                    loading={busy}
                  >
                    {l("重新检查", "Scan again")}
                  </Button>
                  <Button variant="ghost" onClick={() => setManual(true)}>
                    {l("手动指定文件夹", "Choose a folder manually")}
                  </Button>
                </div>
              </div>
            ) : (
              <div className="stack">
                <div className="grid-2">
                  <Field label={l("宿主类型", "Host kind")} htmlFor="manual-kind">
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
                    <Input
                      id="manual-install"
                      value={installPath}
                      onChange={(event) => setInstallPath(event.target.value)}
                      placeholder="/usr/local/bin/dsh"
                      spellCheck={false}
                    />
                  </Field>
                </div>
                <Field label={l("配置目录（可选）", "Config folder (optional)")} htmlFor="manual-config">
                  <Input
                    id="manual-config"
                    value={configPath}
                    onChange={(event) => setConfigPath(event.target.value)}
                    placeholder="~/.dsh"
                    spellCheck={false}
                  />
                </Field>
                <Field label={l("项目文件夹（可选）", "Project folder (optional)")} htmlFor="manual-project">
                  <Input
                    id="manual-project"
                    value={projectRoot}
                    onChange={(event) => setProjectRoot(event.target.value)}
                    placeholder="/path/to/project"
                    spellCheck={false}
                  />
                </Field>
                <Button variant="ghost" onClick={() => setManual(false)}>
                  {l("返回自动发现", "Back to automatic discovery")}
                </Button>
              </div>
            )}
            <div className="onboarding-nav">
              <Button variant="ghost" onClick={() => setStep(1)} disabled={busy}>
                {l("稍后再说", "Later")}
              </Button>
              <Button
                onClick={adapt}
                loading={busy}
                icon="arrow"
                disabled={!manual && !selected && installed.length === 0}
              >
                {installed.length > 0 ? l("继续", "Continue") : l("适配并继续", "Adapt and continue")}
              </Button>
            </div>
          </section>
        ) : null}

        {step === 1 ? (
          <section className="stack" aria-labelledby="onboard-model">
            <div>
              <h1 id="onboard-model">{l("接入递归模型", "Connect a recursive model")}</h1>
              <p className="sub mt-8">
                {l(
                  "这是用于“想出改进”的模型，不会改变宿主平时干活的执行模型。支持 BYOK 和本地 Ollama。",
                  "This model generates improvements. It does not change the model your host uses for daily work. BYOK and local Ollama are supported.",
                )}
              </p>
            </div>
            {providers.length > 0 ? (
              <div className="stack-sm">
                <strong>{l("已配置的服务商", "Configured providers")}</strong>
                {providers.map((provider) => (
                  <button
                    key={provider.id}
                    type="button"
                    className={`option ${chosenProvider?.id === provider.id ? "is-active" : ""}`}
                    onClick={() => setProviderId(provider.id)}
                  >
                    <div className="spread">
                      <strong>{provider.label}</strong>
                      <span className="badge badge-outline">{provider.model}</span>
                    </div>
                    <p className="sub">
                      {provider.key_hint ? l(`Key 尾号 ${provider.key_hint}`, `Key ends in ${provider.key_hint}`) : l("本地模型", "Local model")}
                    </p>
                  </button>
                ))}
              </div>
            ) : null}
            {providers.length === 0 ? (
              <>
                <div className="radio-cards" role="radiogroup" aria-label={l("模型预设", "Model preset")}>
                  {(Object.keys(PRESET_MAP) as (keyof typeof PRESET_MAP)[]).map((key) => {
                    const item = PRESET_MAP[key];
                    const meta = presetCopy[key];
                    return (
                      <button
                        key={key}
                        type="button"
                        role="radio"
                        aria-checked={preset === key}
                        className={`radio-card ${preset === key ? "is-active" : ""}`}
                        onClick={() => setPreset(key)}
                      >
                        <span className="radio-dot" aria-hidden="true">
                          {preset === key ? <span className="radio-dot-inner" /> : null}
                        </span>
                        <span className="radio-body">
                          <strong>{lang === "zh" ? item.zh : item.en}</strong>
                          <span className="sub">{meta?.rationale || ""}</span>
                          <span className="radio-detail">
                            {providerLabel(item.provider)} · {providerModel(item.provider)}
                          </span>
                        </span>
                      </button>
                    );
                  })}
                </div>
                <ProviderForm key={templateId} initialCatalogId={templateId} onSaved={providerSaved} />
              </>
            ) : null}
            <div className="onboarding-nav">
              <Button variant="ghost" onClick={() => setStep(0)}>
                {l("上一步", "Back")}
              </Button>
              <div className="row">
                <Button variant="ghost" onClick={() => setStep(2)}>
                  {l("稍后配置", "Later")}
                </Button>
                {providers.length > 0 ? (
                  <Button onClick={chooseExisting} icon="arrow">
                    {l("使用这个模型", "Use this model")}
                  </Button>
                ) : null}
              </div>
            </div>
          </section>
        ) : null}

        {step === 2 ? (
          <section className="stack" aria-labelledby="onboard-budget">
            <div>
              <h1 id="onboard-budget">{l("预算与隐私", "Budget & privacy")}</h1>
              <p className="sub mt-8">
                {l(
                  "这是自动学习的三层硬上限。超限会直接停止，不会先花钱后通知。",
                  "These are hard limits for automatic learning. When reached, morphd stops before spending.",
                )}
              </p>
            </div>
            <div className="grid-3">
              <Field label={l("月度上限（美元）", "Monthly limit (USD)")} htmlFor="onboard-monthly">
                <Input
                  id="onboard-monthly"
                  type="number"
                  min={0}
                  step="0.5"
                  value={monthly}
                  onChange={(event) => setMonthly(Number(event.target.value))}
                />
              </Field>
              <Field label={l("单会话上限（美元）", "Per-session limit (USD)")} htmlFor="onboard-session">
                <Input
                  id="onboard-session"
                  type="number"
                  min={0}
                  step="0.1"
                  value={perSession}
                  onChange={(event) => setPerSession(Number(event.target.value))}
                />
              </Field>
              <Field label={l("单次分析上限（美元）", "Per-analysis limit (USD)")} htmlFor="onboard-job">
                <Input
                  id="onboard-job"
                  type="number"
                  min={0}
                  step="0.1"
                  value={perJob}
                  onChange={(event) => setPerJob(Number(event.target.value))}
                />
              </Field>
            </div>
            <div className="grid-2">
              <Field label={l("数据保留", "Data retention")} htmlFor="onboard-retention">
                <select
                  id="onboard-retention"
                  className="input select"
                  value={retention}
                  onChange={(event) => setRetention(Number(event.target.value))}
                >
                  <option value={7}>{l("7 天", "7 days")}</option>
                  <option value={30}>{l("30 天（推荐）", "30 days (recommended)")}</option>
                  <option value={90}>{l("90 天", "90 days")}</option>
                  <option value={0}>{l("永久（不推荐）", "Forever (not recommended)")}</option>
                </select>
              </Field>
              <Toggle
                checked={enabled}
                onChange={setEnabled}
                label={l("启用自动学习", "Enable automatic learning")}
                hint={l(
                  "每次会话结束后自动总结；没有信号时零成本跳过。",
                  "Summarize after each session; zero-cost skip when there is no signal.",
                )}
              />
            </div>
            <InfoNote icon="shield">
              {l(
                "默认不上传遥测、不上传会话原文；发送给模型前会脱敏并提供预览。你的 Key 只保存在本机。",
                "No telemetry or full-session upload by default; content is redacted and previewable before sending. Keys stay on this machine.",
              )}
            </InfoNote>
            <div className="onboarding-nav">
              <Button variant="ghost" onClick={() => setStep(1)}>
                {l("上一步", "Back")}
              </Button>
              <Button onClick={finish} loading={busy} icon="check">
                {l("完成，进入首页", "Finish and open Home")}
              </Button>
            </div>
          </section>
        ) : null}
      </main>
    </div>
  );
}
