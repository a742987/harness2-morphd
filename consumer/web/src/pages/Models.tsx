import { useMemo, useState } from "react";
import { api } from "../api";
import { ProviderForm } from "../components/ProviderForm";
import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  ErrorBanner,
  InfoNote,
  Input,
  Modal,
  PageHeader,
  Section,
  Select,
  Toggle,
} from "../components/UI";
import { formatMoney, useL, useLang } from "../i18n";
import { useApp } from "../state";
import type { Provider, ProviderTestResult } from "../types";

const ROLE_LABELS: Record<string, { zh: string; en: string; hintZh: string; hintEn: string }> = {
  proposer: {
    zh: "递归提案模型",
    en: "Proposer",
    hintZh: "生成 Agent 工作方式的改进建议；必须支持工具或 JSON 补丁模式。",
    hintEn: "Generates agent improvements; must support tools or JSON patch mode.",
  },
  composer: {
    zh: "合成模型",
    en: "Composer",
    hintZh: "把候选合成为最终建议；默认与递归模型相同。",
    hintEn: "Combines candidates into the final suggestion; defaults to the proposer.",
  },
  selector: {
    zh: "选择模型",
    en: "Selector",
    hintZh: "需要时在候选之间选择；默认与递归模型相同。",
    hintEn: "Selects between candidates when needed; defaults to the proposer.",
  },
  judge: {
    zh: "判分模型（可选）",
    en: "Judge (optional)",
    hintZh: "只在启用 LLM 判分时使用；默认使用确定性判据。",
    hintEn: "Only used for LLM scoring; deterministic checks are the default.",
  },
};

export function Models() {
  const app = useApp();
  const l = useL();
  const lang = useLang();
  const [addOpen, setAddOpen] = useState(false);
  const [addTemplate, setAddTemplate] = useState<string | undefined>(undefined);
  const [editing, setEditing] = useState<Provider | null>(null);
  const [deleting, setDeleting] = useState<Provider | null>(null);
  const [deleteAllOpen, setDeleteAllOpen] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ provider: string; result: ProviderTestResult } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [passphrase, setPassphrase] = useState("");

  const providers = app.providers?.providers || [];
  const catalog = app.providers?.catalog || [];
  const presets = app.providers?.presets || {};
  const presetProvider: Record<string, string> = { frugal: "deepseek", balanced: "openai", quality: "anthropic", local: "ollama" };
  const perCallEstimate = (id: string): string => {
    const item = catalog.find((entry) => entry.id === id);
    const price = item?.price;
    if (!price || !item) return "";
    const usd = (20000 * price.input_per_million + 4000 * price.output_per_million) / 1_000_000;
    return l(`约 ${formatMoney(usd, lang)} / 次（估算）`, `~${formatMoney(usd, lang)} / run (est.)`);
  };
  const settings = app.settings;
  const options = useMemo(() => {
    const items = [{ value: "", label: l("未选择", "Not set") }];
    for (const provider of providers) {
      items.push({
        value: `${provider.id}/${provider.model}`,
        label: `${provider.label} · ${provider.model}`,
      });
    }
    return items;
  }, [providers, l]);

  const saveRole = async (role: "proposer" | "composer" | "selector" | "judge", value: string) => {
    if (!settings) return;
    const models = { ...settings.models };
    if (role === "judge") models.judge = value || null;
    else models[role] = value;
    await app.saveSettings({ models });
    app.toast("ok", "模型角色已保存", "Model role saved");
  };

  const applyPreset = async (preset: "frugal" | "balanced" | "quality" | "local") => {
    if (!settings) return;
    const template = catalog.find((item) => item.id === presetProvider[preset]);
    const matching = providers.find((provider) =>
      template ? provider.protocol === template.protocol && provider.model === template.default_model : false,
    ) || (preset === "local" ? providers.find((provider) => provider.local) : undefined);
    if (!matching) {
      setAddTemplate(presetProvider[preset]);
      setAddOpen(true);
      app.toast(
        "info",
        "先接入预设推荐的服务商，再自动分配角色",
        "Add the recommended provider first; roles will be assigned automatically",
      );
      return;
    }
    await app.saveSettings({
      models: {
        ...settings.models,
        preset,
        proposer: `${matching.id}/${matching.model}`,
        composer: `${matching.id}/${matching.model}`,
        selector: `${matching.id}/${matching.model}`,
      },
      analysis: {
        ...settings.analysis,
        depth: preset === "frugal" ? "light" : preset === "quality" ? "full" : "adaptive",
      },
    });
    app.toast("ok", "预设已应用", "Preset applied");
  };

  const test = async (provider: Provider) => {
    setTestingId(provider.id);
    setError(null);
    try {
      const result = await api.testProvider(provider.id, {});
      setTestResult({ provider: provider.label, result });
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setTestingId(null);
    }
  };

  const remove = async () => {
    if (!deleting) return;
    setBusy(true);
    try {
      await api.deleteProvider(deleting.id);
      await app.refreshData();
      setDeleting(null);
      app.toast("ok", "服务商与 Key 引用已删除", "Provider and key reference deleted");
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const removeAll = async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await api.deleteAllProviders();
      await app.refreshData();
      setDeleteAllOpen(false);
      app.toast(
        "ok",
        `已删除 ${result.deleted} 个服务商及其 Key 引用`,
        `Deleted ${result.deleted} providers and their key references`,
      );
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const unlock = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.vaultUnlock(passphrase);
      setPassphrase("");
      await app.refreshData();
      app.toast("ok", "保险库已解锁", "Vault unlocked");
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    } finally {
      setBusy(false);
    }
  };

  const lock = async () => {
    try {
      await api.vaultLock();
      await app.refreshData();
      app.toast("ok", "保险库已锁定", "Vault locked");
    } catch (caught) {
      setError(caught);
      app.handleError(caught);
    }
  };

  return (
    <>
      <PageHeader
        title={l("模型与 BYOK", "Models & BYOK")}
        subtitle={l(
          "你自带服务商与 Key。递归模型只用于生成改进，不改变宿主日常执行模型。",
          "Bring your own provider and key. The recursive model only proposes improvements; it does not change your host's daily model.",
        )}
        actions={
          <Button icon="plus" onClick={() => { setAddTemplate(undefined); setAddOpen(true); }}>
            {l("添加服务商", "Add provider")}
          </Button>
        }
      />
      {error ? <ErrorBanner error={app.handleError(error)} /> : null}

      <Section
        title={l("模型预设", "Presets")}
        subtitle={l("预设只影响递归/合成/判分，不改变执行模型。", "Presets only affect proposer/composer/judge, not the executor.")}
      >
        <div className="option-grid">
          {(["frugal", "balanced", "quality", "local"] as const).map((preset) => (
            <button
              key={preset}
              type="button"
              className={`option ${settings?.models.preset === preset ? "is-active" : ""}`}
              onClick={() => void applyPreset(preset)}
            >
              <div className="spread">
                <strong>{presets[preset]?.label || preset}</strong>
                <Badge variant="outline">{presets[preset]?.analysis_depth || ""}</Badge>
              </div>
              <p className="sub">{presets[preset]?.rationale || ""}</p>
              <p className="sub">
                {catalog.find((item) => item.id === presetProvider[preset])?.label || presetProvider[preset]}
                {" · "}
                {catalog.find((item) => item.id === presetProvider[preset])?.model || presets[preset]?.provider || ""}
              </p>
              <p className="small">{perCallEstimate(presetProvider[preset])}</p>
            </button>
          ))}
        </div>
      </Section>

      <Section
        title={l("服务商", "Providers")}
        subtitle={l("Key 只保存在本机 Keychain 或加密保险库；界面只显示尾号。", "Keys stay in the OS keychain or encrypted vault; only the last digits are shown.")}
        actions={
          providers.length > 0 ? (
            <Button size="sm" variant="ghost" icon="trash" onClick={() => setDeleteAllOpen(true)}>
              {l("删除全部 Key", "Delete all keys")}
            </Button>
          ) : undefined
        }
      >
        {providers.length === 0 ? (
          <EmptyState
            icon="key"
            title={l("还没有接入模型", "No model connected")}
            action={
              <Button icon="plus" onClick={() => setAddOpen(true)}>
                {l("接入服务商", "Connect a provider")}
              </Button>
            }
          >
            {l(
              "可以接 OpenAI、Anthropic、Gemini、DeepSeek、OpenRouter、Ollama 或任意 OpenAI-compatible 端点。",
              "Use OpenAI, Anthropic, Gemini, DeepSeek, OpenRouter, Ollama, or any OpenAI-compatible endpoint.",
            )}
          </EmptyState>
        ) : (
          <div className="grid-2">
            {providers.map((provider) => (
              <article className="provider-card" key={provider.id}>
                <div className="provider-head">
                  <div className="provider-title">
                    <span className="provider-mark" aria-hidden="true">
                      {provider.label.slice(0, 1).toUpperCase()}
                    </span>
                    <div>
                      <div className="provider-name">{provider.label}</div>
                      <div className="provider-id">{provider.id} · {provider.protocol}</div>
                    </div>
                  </div>
                  <Badge variant={provider.local ? "accent" : "outline"}>
                    {provider.local ? l("本地", "Local") : l("云端", "Cloud")}
                  </Badge>
                </div>
                <div className="stack-sm">
                  <div className="spread">
                    <span className="small strong">{l("模型", "Model")}</span>
                    <span className="mono small wrap-anywhere">{provider.model}</span>
                  </div>
                  <div className="spread">
                    <span className="small strong">{l("Key", "Key")}</span>
                    <span className="small">
                      {provider.local ? l("本地模型，无需 Key", "Local model, no key needed") : provider.has_key ? l(`已保存 · 尾号 ${provider.key_hint}`, `Saved · ends ${provider.key_hint}`) : l("未保存", "Not saved")}
                    </span>
                  </div>
                  <div className="spread">
                    <span className="small strong">{l("价格", "Price")}</span>
                    <span className="small">
                      {provider.price.confirmed
                        ? l(
                            `输入 ${formatMoney(provider.price.input_per_million, lang)}/M · 输出 ${formatMoney(provider.price.output_per_million, lang)}/M`,
                            `In ${formatMoney(provider.price.input_per_million, lang)}/M · out ${formatMoney(provider.price.output_per_million, lang)}/M`,
                          )
                        : provider.price.input_per_million > 0 || provider.price.output_per_million > 0
                          ? l("参考价，以账单为准", "Reference price; bill is authoritative")
                          : l("未知价格：不会启动分析", "Unknown price: analysis blocked")}
                    </span>
                  </div>
                  <p className="sub">{provider.data_policy}</p>
                </div>
                <div className="row">
                  <Button size="sm" variant="ghost" icon="wifi" onClick={() => void test(provider)} loading={testingId === provider.id}>
                    {l("测试连接", "Test")}
                  </Button>
                  <Button size="sm" variant="ghost" icon="advanced" onClick={() => setEditing(provider)}>
                    {l("编辑", "Edit")}
                  </Button>
                  <Button size="sm" variant="ghost" icon="trash" onClick={() => setDeleting(provider)}>
                    {l("删除", "Delete")}
                  </Button>
                </div>
              </article>
            ))}
          </div>
        )}
      </Section>

      <Section
        title={l("模型角色", "Model roles")}
        subtitle={l("每个角色可独立选择；fallback 只用于鉴权、限流、超时或服务不可用。", "Roles are independent; fallbacks apply only to auth, rate limits, timeouts, or outages.")}
      >
        {settings ? (
          <div className="stack">
            {(["proposer", "composer", "selector", "judge"] as const).map((role) => (
              <div key={role} className="grid-2">
                <div>
                  <strong>{ROLE_LABELS[role][lang]}</strong>
                  <p className="sub">{lang === "zh" ? ROLE_LABELS[role].hintZh : ROLE_LABELS[role].hintEn}</p>
                </div>
                <Select
                  value={(settings.models[role] as string) || ""}
                  onChange={(event) => void saveRole(role, event.target.value)}
                  aria-label={ROLE_LABELS[role][lang]}
                >
                  {options.map((option) => (
                    <option key={option.value || "none"} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </Select>
              </div>
            ))}
            <div className="grid-2">
              <div>
                <strong>{l("执行模型（只读）", "Executor (read-only)")}</strong>
                <p className="sub">
                  {l(
                    "宿主当前使用的模型。harness2-morphd 不替换它；高级覆盖会重置学习基线。",
                    "The model your host uses. harness2-morphd does not replace it; overriding resets the learning baseline.",
                  )}
                </p>
              </div>
              <div className="badge badge-dashed">{l("由宿主决定", "Chosen by host")}</div>
            </div>
            <div className="grid-2">
              <div>
                <strong>{l("Fallback 顺序", "Fallback order")}</strong>
                <p className="sub">{l("最多 3 个，依次降级。", "Up to 3, tried in order.")}</p>
              </div>
              <div className="stack-sm">
                {[0, 1, 2].map((index) => (
                  <Select
                    key={index}
                    value={settings.models.fallbacks[index] || ""}
                    onChange={(event) => {
                      const next = [...settings.models.fallbacks];
                      next[index] = event.target.value;
                      void app.saveSettings({
                        models: { ...settings.models, fallbacks: next.filter(Boolean).slice(0, 3) },
                      });
                    }}
                    aria-label={`fallback ${index + 1}`}
                  >
                    <option value="">{l("无", "None")}</option>
                    {options.slice(1).map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </Select>
                ))}
              </div>
            </div>
          </div>
        ) : null}
      </Section>

      <Section
        title={l("Key 与保险库", "Keys & vault")}
        subtitle={l("系统 Keychain 不可用时，使用加密保险库；Key 不会进入日志、导出或浏览器存储。", "If the OS keychain is unavailable, the encrypted vault is used. Keys never enter logs, exports, or browser storage.")}
      >
        <div className="grid-2">
          <div className="stack-sm">
            <div className="spread">
              <span>{l("系统 Keychain", "OS keychain")}</span>
              <Badge variant={app.providers?.vault.keychain ? "accent" : "dashed"}>
                {app.providers?.vault.keychain ? l("可用", "Available") : l("不可用", "Unavailable")}
              </Badge>
            </div>
            <div className="spread">
              <span>{l("本地加密保险库", "Encrypted local vault")}</span>
              <Badge variant={app.providers?.vault.vault_unlocked ? "accent" : "dashed"}>
                {app.providers?.vault.vault_unlocked ? l("已解锁", "Unlocked") : l("已锁定", "Locked")}
              </Badge>
            </div>
            <p className="sub">
              {l(
                "保险库口令只在本机内存中使用。锁定后，需要 Key 的操作会暂停。",
                "The vault passphrase stays in local memory. When locked, operations needing keys pause.",
              )}
            </p>
          </div>
          {!app.providers?.vault.keychain ? (
            <div className="stack-sm">
              {app.providers?.vault.vault_unlocked ? (
                <Button variant="ghost" icon="lock" onClick={lock}>
                  {l("锁定保险库", "Lock vault")}
                </Button>
              ) : (
                <>
                  <Input
                    type="password"
                    value={passphrase}
                    onChange={(event) => setPassphrase(event.target.value)}
                    placeholder={l("保险库口令", "Vault passphrase")}
                    autoComplete="new-password"
                  />
                  <Button icon="lock" onClick={unlock} loading={busy} disabled={!passphrase}>
                    {l("解锁保险库", "Unlock vault")}
                  </Button>
                </>
              )}
            </div>
          ) : (
            <InfoNote tone="ok" icon="lock">
              {l("Key 由系统钥匙串管理。", "Keys are managed by the OS keychain.")}
            </InfoNote>
          )}
        </div>
      </Section>

      <Section title={l("隐私与费用", "Privacy & cost")} subtitle={l("参考价，以服务商实际账单为准。", "Reference prices; the provider bill is authoritative.")}>
        <div className="stack">
          <Toggle
            checked={Boolean(settings?.privacy.local_only)}
            onChange={(next) => {
              if (!settings) return;
              void app.saveSettings({ privacy: { ...settings.privacy, local_only: next } });
            }}
            label={l("仅使用本地模型", "Local models only")}
            hint={l(
              "开启后禁止任何云端服务商；未配置 Ollama 等本地端点时分析会停止并提示。",
              "Blocks every cloud provider. If no local endpoint is configured, analysis stops with an explanation.",
            )}
          />
          <InfoNote icon="budget">
            {l(
              "未知价格不会按 0 计费。每次分析前会做最坏情况估算，超过单次/单会话/月度预算就不启动。",
              "An unknown price is never treated as zero. Each analysis is pre-checked against job, session, and monthly limits before any call.",
            )}
          </InfoNote>
        </div>
      </Section>

      {addOpen ? (
        <Modal
          title={l("添加模型服务商", "Add provider")}
          subtitle={l("先测试、后保存；测试使用临时 Key，不写入 Keychain。", "Test first, then save; testing uses a temporary key and does not write the keychain.")}
          onClose={() => setAddOpen(false)}
          wide
        >
          <ProviderForm
            initialCatalogId={addTemplate}
            onSaved={async () => {
              setAddOpen(false);
              await app.refreshData();
            }}
            onCancel={() => setAddOpen(false)}
          />
        </Modal>
      ) : null}

      {editing ? (
        <Modal
          title={l(`编辑 ${editing.label}`, `Edit ${editing.label}`)}
          onClose={() => setEditing(null)}
          wide
        >
          <ProviderForm
            existing={editing}
            onSaved={async () => {
              setEditing(null);
              await app.refreshData();
            }}
            onCancel={() => setEditing(null)}
          />
        </Modal>
      ) : null}

      {deleting ? (
        <ConfirmDialog
          title={l("删除这个服务商？", "Delete this provider?")}
          confirmLabel={l("删除服务商与 Key 引用", "Delete provider and key reference")}
          onConfirm={remove}
          onClose={() => setDeleting(null)}
          danger
          busy={busy}
        >
          <p>{l(`将从本机删除 ${deleting.label} 的配置与 Key 引用。`, `This removes ${deleting.label}'s configuration and key reference from this machine.`)}</p>
          <p className="sub">
            {l(
              "使用这个服务商的角色会停止运行，直到你重新配置。不会影响宿主自身。",
              "Roles using it stop until reconfigured. Your host itself is not affected.",
            )}
          </p>
        </ConfirmDialog>
      ) : null}

      {deleteAllOpen ? (
        <ConfirmDialog
          title={l("删除全部服务商与 Key？", "Delete all providers and keys?")}
          confirmLabel={l("删除全部 Key 引用", "Delete all key references")}
          onConfirm={removeAll}
          onClose={() => setDeleteAllOpen(false)}
          danger
          busy={busy}
        >
          <p>
            {l(
              `将从本机删除 ${providers.length} 个服务商配置与 Key 引用。所有模型角色会停止运行，直到重新配置。`,
              `Removes ${providers.length} provider configurations and key references from this machine. All model roles stop until reconfigured.`,
            )}
          </p>
          <p className="sub">
            {l(
              "这不会删除会话、历史、设置预算或宿主文件；删除后无法恢复 Key。",
              "Sessions, history, budgets, and host files are kept. Deleted keys cannot be recovered.",
            )}
          </p>
        </ConfirmDialog>
      ) : null}

      {testResult ? (
        <Modal
          title={l("连接测试结果", "Connection test result")}
          subtitle={testResult.provider}
          onClose={() => setTestResult(null)}
          footer={
            <Button variant="ghost" onClick={() => setTestResult(null)}>
              {l("关闭", "Close")}
            </Button>
          }
        >
          <InfoNote tone={testResult.result.ok ? "ok" : "warn"} icon={testResult.result.ok ? "check" : "alert"}>
            {testResult.result.ok
              ? l("鉴权、模型和最小对话均通过。", "Authentication, model, and minimal chat passed.")
              : l("有步骤未通过；请查看下面的原因。", "One or more steps failed; see details below.")}
          </InfoNote>
          <ul className="step-list">
            {testResult.result.steps.map((step) => (
              <li key={step.name} className={step.ok ? "step-ok" : "step-bad"}>
                <span>{step.ok ? "✓" : "!"}</span>
                <span>
                  <strong>
                    {{ endpoint: l("端点", "Endpoint"), auth: l("鉴权", "Auth"), model: l("模型", "Model"), chat: l("最小对话", "Minimal chat"), tools: l("工具调用", "Tools") }[step.name] || step.name}
                  </strong>{" "}
                  · {step.detail}
                </span>
              </li>
            ))}
          </ul>
          <p className="sub">
            {l("延迟", "Latency")}: {testResult.result.latency_ms} ms · {l("测试花费", "Test cost")}:{" "}
            {formatMoney(testResult.result.estimated_cost_usd, lang)} ·{" "}
            {l("工具模式", "Tool mode")}: {String(testResult.result.tool_mode_possible)}
          </p>
        </Modal>
      ) : null}
    </>
  );
}
