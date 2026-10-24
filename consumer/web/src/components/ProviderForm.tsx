import { useMemo, useState } from "react";
import { api, ApiError } from "../api";
import { useL, useLang } from "../i18n";
import { useApp } from "../state";
import type { Provider, ProviderTestResult } from "../types";
import { Button, ErrorBanner, ExternalLink, Field, InfoNote, Input, Select, Toggle } from "./UI";

interface FormState {
  label: string;
  protocol: "openai-compatible" | "anthropic" | "gemini";
  base_url: string;
  model: string;
  api_key: string;
  context_window: number;
  max_output: number;
  supports_tools: boolean;
  supports_json: boolean;
  input_price: number;
  output_price: number;
  per_call: number;
  confirmed: boolean;
  data_policy_ack: boolean;
  data_policy: string;
  help_url: string;
}

function isLocalUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return ["localhost", "127.0.0.1", "::1"].includes(parsed.hostname);
  } catch {
    return false;
  }
}

function fromProvider(provider: Partial<Provider> | null, template?: Record<string, unknown>): FormState {
  const source = provider || template || {};
  const price = (source.price as Record<string, number> | undefined) || {};
  return {
    label: String(source.label || "Custom"),
    protocol: (source.protocol as FormState["protocol"]) || "openai-compatible",
    base_url: String(source.base_url || ""),
    model: String(source.model || ""),
    api_key: "",
    context_window: Number(source.context_window || 128000),
    max_output: Number(source.max_output || 4096),
    supports_tools: Boolean(source.supports_tools ?? true),
    supports_json: Boolean(source.supports_json ?? true),
    input_price: Number(price.input_per_million || 0),
    output_price: Number(price.output_per_million || 0),
    per_call: Number(price.per_call_usd || 0),
    confirmed: Boolean(price.confirmed || false),
    data_policy_ack: Boolean(source.data_policy_ack || isLocalUrl(String(source.base_url || ""))),
    data_policy: String(source.data_policy || ""),
    help_url: String(source.help_url || ""),
  };
}

export function ProviderForm({
  existing,
  initialCatalogId,
  onSaved,
  onCancel,
}: {
  existing?: Provider;
  initialCatalogId?: string;
  onSaved: (provider: Provider) => void;
  onCancel?: () => void;
}) {
  const app = useApp();
  const l = useL();
  const lang = useLang();
  const catalog = app.providers?.catalog || [];
  const initialTemplate = initialCatalogId
    ? catalog.find((item) => item.id === initialCatalogId)
    : undefined;
  const [form, setForm] = useState<FormState>(() =>
    fromProvider(existing || null, initialTemplate as unknown as Record<string, unknown> | undefined),
  );
  const [busy, setBusy] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<ProviderTestResult | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [vaultPassphrase, setVaultPassphrase] = useState("");
  const [vaultNeeded, setVaultNeeded] = useState(false);
  const local = useMemo(() => isLocalUrl(form.base_url), [form.base_url]);
  const vault = app.providers?.vault;
  const vaultLocked = Boolean(vault && !vault.keychain && !vault.vault_unlocked);
  const requiresKey = !local;

  const patch = (next: Partial<FormState>) => {
    setForm((current) => ({ ...current, ...next }));
    setResult(null);
  };

  const payload = () => ({
    label: form.label,
    protocol: form.protocol,
    base_url: form.base_url,
    model: form.model,
    context_window: form.context_window,
    max_output: form.max_output,
    supports_tools: form.supports_tools,
    supports_json: form.supports_json,
    data_policy: form.data_policy,
    help_url: form.help_url,
    data_policy_ack: form.data_policy_ack || local,
    local,
    price: {
      input_per_million: form.input_price,
      cached_input_per_million: form.input_price * 0.1,
      cache_write_per_million: form.input_price,
      output_per_million: form.output_price,
      reasoning_per_million: form.output_price,
      per_call_usd: form.per_call,
      confirmed: form.confirmed || local,
    },
    ...(form.api_key ? { api_key: form.api_key } : {}),
  });

  const applyTemplate = (id: string) => {
    const template = catalog.find((item) => item.id === id);
    if (!template) return;
    setForm((current) => ({
      ...fromProvider(null, template as unknown as Record<string, unknown>),
      api_key: current.api_key,
    }));
    setResult(null);
  };

  const test = async () => {
    setTesting(true);
    setError(null);
    try {
      const next = await api.testProvider(existing?.id || null, payload());
      setResult(next);
    } catch (caught) {
      const apiError = app.handleError(caught);
      setError(apiError);
    } finally {
      setTesting(false);
    }
  };

  const unlock = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.vaultUnlock(vaultPassphrase);
      setVaultNeeded(false);
      setVaultPassphrase("");
      await app.refreshData();
      app.toast("ok", "保险库已解锁", "Vault unlocked");
    } catch (caught) {
      setError(app.handleError(caught));
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!form.label.trim() || !form.base_url.trim() || !form.model.trim()) {
      app.toast("warn", "请填写名称、Base URL 和模型 ID", "Fill in name, Base URL and model ID");
      return;
    }
    if (requiresKey && !form.api_key && !existing?.has_key) {
      app.toast("warn", "云端服务商需要 API Key", "A cloud provider needs an API key");
      return;
    }
    if (!form.data_policy_ack && !local) {
      app.toast("warn", "请先确认数据政策", "Confirm the data policy first");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const body = payload();
      const saved = existing
        ? await api.updateProvider(existing.id, body)
        : await api.addProvider(body);
      app.toast("ok", "模型服务商已保存", "Provider saved");
      await app.refreshData();
      onSaved(saved);
    } catch (caught) {
      const apiError = app.handleError(caught);
      setError(apiError);
      if (apiError?.code === "VAULT_LOCKED") setVaultNeeded(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      {error ? <ErrorBanner error={error} /> : null}
      {!existing && catalog.length > 0 ? (
        <div className="stack-sm">
          <span className="small strong">{l("从模板开始", "Start from a template")}</span>
          <div className="row">
            {catalog.map((item) => (
              <button
                key={item.id}
                type="button"
                className="badge badge-outline"
                onClick={() => applyTemplate(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      <div className="grid-2">
        <Field label={l("名称", "Name")} htmlFor="provider-label">
          <Input
            id="provider-label"
            value={form.label}
            onChange={(event) => patch({ label: event.target.value })}
            maxLength={80}
          />
        </Field>
        <Field label={l("协议", "Protocol")} htmlFor="provider-protocol">
          <Select
            id="provider-protocol"
            value={form.protocol}
            onChange={(event) => patch({ protocol: event.target.value as FormState["protocol"] })}
          >
            <option value="openai-compatible">OpenAI-compatible</option>
            <option value="anthropic">Anthropic</option>
            <option value="gemini">Google Gemini</option>
          </Select>
        </Field>
      </div>
      <Field
        label={l("Base URL", "Base URL")}
        hint={l(
          "Ollama / LM Studio 用 http://127.0.0.1:11434/v1；不会自动扫描网络。",
          "For Ollama / LM Studio use http://127.0.0.1:11434/v1; no network scanning.",
        )}
        htmlFor="provider-url"
      >
        <Input
          id="provider-url"
          value={form.base_url}
          onChange={(event) => patch({ base_url: event.target.value, data_policy_ack: isLocalUrl(event.target.value) ? true : form.data_policy_ack })}
          placeholder="https://api.example.com/v1"
          spellCheck={false}
          autoComplete="off"
        />
      </Field>
      <div className="grid-2">
        <Field
          label={l("模型 ID", "Model ID")}
          hint={local ? l("必须与 Ollama 中的模型名一致", "Must match the model name in Ollama") : undefined}
          htmlFor="provider-model"
        >
          <Input
            id="provider-model"
            value={form.model}
            onChange={(event) => patch({ model: event.target.value })}
            placeholder="model-name"
            spellCheck={false}
            autoComplete="off"
          />
        </Field>
        <Field
          label={local ? l("API Key（本地可留空）", "API key (optional for local)") : l("API Key", "API key")}
          hint={
            existing?.has_key
              ? l(
                  `已保存，尾号 ${existing.key_hint || ""}；留空则保持不变。`,
                  `Saved, ending in ${existing.key_hint || ""}; leave blank to keep it.`,
                )
              : l("只保存在本机 Keychain 或加密保险库。", "Stored only in the OS keychain or encrypted vault.")
          }
          htmlFor="provider-key"
        >
          <Input
            id="provider-key"
            type="password"
            value={form.api_key}
            onChange={(event) => patch({ api_key: event.target.value })}
            placeholder={local ? l("可留空", "Optional") : "sk-…"}
            autoComplete="new-password"
            spellCheck={false}
          />
        </Field>
      </div>
      {form.help_url ? (
        <div className="small">
          <ExternalLink href={form.help_url}>{l("如何获取这个服务商的 Key", "How to get this provider's key")}</ExternalLink>
        </div>
      ) : null}
      <div className="note">
        <div>
          <strong>{l("数据会发给谁", "Where data goes")}</strong>
          <p className="sub">
            {form.data_policy ||
              l(
                "请求会直接发送到你填写的端点，使用你的 Key；harness2-morphd 不做代理，也不转售模型调用。",
                "Requests go directly to your endpoint with your key; harness2-morphd does not proxy or resell model calls.",
              )}
          </p>
        </div>
      </div>
      <label className="row small">
        <input
          type="checkbox"
          checked={form.data_policy_ack || local}
          onChange={(event) => patch({ data_policy_ack: event.target.checked })}
          disabled={local}
        />
        {l(
          "我已阅读并确认该服务商的数据处理/保留政策",
          "I have reviewed this provider's data processing and retention policy",
        )}
      </label>
      <details
        className="stack-sm"
        open={showAdvanced}
        onToggle={(event) => setShowAdvanced((event.target as HTMLDetailsElement).open)}
      >
        <summary className="small strong">{l("高级：上下文、工具与价格", "Advanced: context, tools and price")}</summary>
        <div className="grid-2 mt-8">
          <Field label={l("上下文窗口", "Context window")} htmlFor="provider-context">
            <Input
              id="provider-context"
              type="number"
              min={1024}
              value={form.context_window}
              onChange={(event) => patch({ context_window: Number(event.target.value) })}
            />
          </Field>
          <Field label={l("最大输出 tokens", "Max output tokens")} htmlFor="provider-output">
            <Input
              id="provider-output"
              type="number"
              min={64}
              value={form.max_output}
              onChange={(event) => patch({ max_output: Number(event.target.value) })}
            />
          </Field>
        </div>
        <div className="grid-2">
          <Toggle
            checked={form.supports_tools}
            onChange={(next) => patch({ supports_tools: next })}
            label={l("支持工具调用", "Supports tool calling")}
            hint={l("生成改进建议时优先使用工具模式；不支持时自动用补丁模式。", "Improvement generation prefers tools; patch mode is the fallback.")}
          />
          <Toggle
            checked={form.supports_json}
            onChange={(next) => patch({ supports_json: next })}
            label={l("支持 JSON 输出", "Supports JSON output")}
            hint={l("补丁模式需要完整 JSON 文件内容。", "Patch mode needs complete JSON file contents.")}
          />
        </div>
        <div className="grid-2">
          <Field
            label={l("输入价格（美元/百万 tokens）", "Input price (USD / million tokens)")}
            hint={l("参考价，以服务商账单为准。", "Reference price; the provider's bill is authoritative.")}
            htmlFor="provider-input-price"
          >
            <Input
              id="provider-input-price"
              type="number"
              min={0}
              step="0.01"
              value={form.input_price}
              onChange={(event) => patch({ input_price: Number(event.target.value) })}
            />
          </Field>
          <Field label={l("输出价格（美元/百万 tokens）", "Output price (USD / million tokens)")} htmlFor="provider-output-price">
            <Input
              id="provider-output-price"
              type="number"
              min={0}
              step="0.01"
              value={form.output_price}
              onChange={(event) => patch({ output_price: Number(event.target.value) })}
            />
          </Field>
        </div>
        <Toggle
          checked={form.confirmed || local}
          onChange={(next) => patch({ confirmed: next })}
          label={l("确认价格（未知价格不运行）", "Confirm price (unknown price blocks analysis)")}
          hint={l(
            "未确认且价格为 0 时，harness2-morphd 会拒绝启动分析，避免把未知成本当 0。",
            "When unconfirmed and zero, harness2-morphd refuses to start analysis rather than treating unknown cost as zero.",
          )}
          disabled={local}
        />
      </details>
      {vaultLocked || vaultNeeded ? (
        <div className="note note-warn">
          <div className="stack-sm grow">
            <strong>{l("系统钥匙串不可用：设置本地保险库口令", "OS keychain unavailable: set a local vault passphrase")}</strong>
            <p className="sub">
              {l(
                "口令只在本机内存中使用。忘记口令时需要重新录入 Key。",
                "The passphrase stays in local memory. Forgetting it means re-entering keys.",
              )}
            </p>
            <div className="input-row">
              <Input
                type="password"
                value={vaultPassphrase}
                onChange={(event) => setVaultPassphrase(event.target.value)}
                placeholder={l("保险库口令", "Vault passphrase")}
                autoComplete="new-password"
              />
              <Button size="sm" variant="secondary" onClick={unlock} loading={busy}>
                {l("解锁", "Unlock")}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
      {result ? (
        <div className="stack-sm">
          <InfoNote tone={result.ok ? "ok" : "warn"} icon={result.ok ? "check" : "alert"}>
            <strong>
              {result.ok
                ? l("连接测试通过", "Connection test passed")
                : l("连接测试未完全通过", "Connection test did not fully pass")}
            </strong>
            <p className="sub">
              {l(
                `延迟约 ${result.latency_ms} ms；测试花费约 ${result.estimated_cost_usd} 美元。`,
                `Latency ~${result.latency_ms} ms; test cost ~$${result.estimated_cost_usd}.`,
              )}
            </p>
          </InfoNote>
          <ul className="step-list">
            {result.steps.map((step) => (
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
        </div>
      ) : null}
      <div className="row">
        <Button variant="secondary" onClick={test} loading={testing} icon="wifi">
          {l("测试连接（先测试后保存）", "Test connection (before saving)")}
        </Button>
        <Button onClick={save} loading={busy} icon="check">
          {existing ? l("保存修改", "Save changes") : l("保存服务商", "Save provider")}
        </Button>
        {onCancel ? (
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            {l("取消", "Cancel")}
          </Button>
        ) : null}
      </div>
      <p className="sub">
        {l(
          "测试使用临时 Key，不会写入 Keychain；只有点击保存后才持久化。",
          "Testing uses a temporary key and never writes it to the keychain; it is stored only when you save.",
        )}{" "}
        {lang === "zh" ? "提案角色要求模型支持工具或 JSON 补丁模式。" : "Proposer roles require tool or JSON patch support."}
      </p>
    </div>
  );
}
