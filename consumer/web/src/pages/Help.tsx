import { Badge, Button, ExternalLink, InfoNote, PageHeader, Section, Term } from "../components/UI";
import { useL, useLang } from "../i18n";
import { useApp } from "../state";

const KEY_HELP = [
  { label: "OpenAI", url: "https://platform.openai.com/api-keys" },
  { label: "Anthropic", url: "https://console.anthropic.com/settings/keys" },
  { label: "Google AI Studio", url: "https://aistudio.google.com/app/apikey" },
  { label: "DeepSeek", url: "https://platform.deepseek.com/api_keys" },
  { label: "OpenRouter", url: "https://openrouter.ai/keys" },
  { label: "Ollama", url: "https://ollama.com" },
];

const GLOSSARY = [
  { name: "harness", zh: "Agent 的工作方式：提示、技能和脚本。harness2-morphd 会尝试改进这些文件。", en: "How the agent works: instructions, skills, and scripts. harness2-morphd tries to improve these files." },
  { name: "candidate", zh: "一条改进建议。只有你点击“应用”后才会写入真实文件。", en: "An improvement suggestion. Nothing is written until you choose Apply." },
  { name: "recursive", zh: "让 Agent 自己尝试几种改法，再挑出更顺的一种。", en: "Let the agent try a few improvements and select the one that works best." },
  { name: "incognito", zh: "本次会话不记录、不分析、不产生费用。", en: "This session is not recorded, analyzed, or billed." },
  { name: "quiet", zh: "这段时间只把建议放进待看，不主动展示或通知。", en: "Suggestions go to the inbox without proactive display or notification." },
  { name: "trial", zh: "自动应用后的观察期；出现回归信号会自动回滚。", en: "A watch period after auto-apply; regressions trigger an automatic rollback." },
  { name: "triage", zh: "零成本判断：先看这次会话有没有值得分析的问题。", en: "A zero-cost check for whether the session is worth analyzing." },
];

export function Help() {
  const app = useApp();
  const l = useL();
  const lang = useLang();
  return (
    <>
      <PageHeader
        title={l("帮助与关于", "Help and about")}
        subtitle={l(
          "3 分钟上手、Key 获取、撤销与恢复、隐私说明、兼容矩阵与术语表。",
          "Three-minute setup, key help, undo and restore, privacy, compatibility, and glossary.",
        )}
      />

      <Section title={l("3 分钟上手", "Three-minute setup")} subtitle={l("所有日常配置都可以在界面完成，不需要终端或 YAML。", "Every daily setting is available here; no terminal or YAML required.")}>
        <ol className="step-list">
          <li><span className="badge badge-accent">1</span><span>{l("在“宿主”页确认发现结果并适配；找不到时手动选择安装目录。", "Confirm the discovery result on Hosts and adapt; choose a folder manually if needed.")}</span></li>
          <li><span className="badge badge-accent">2</span><span>{l("在“模型”页接入 BYOK 或本地 Ollama，先测试连接再保存；选择递归模型。", "Connect BYOK or local Ollama on Models; test before saving and choose a recursive model.")}</span></li>
          <li><span className="badge badge-accent">3</span><span>{l("在“学习”页确认顾问 + 保守、预算与隐私；正常使用 Agent。", "Confirm Advisor + Conservative, budget, and privacy on Learning; then use your agent normally.")}</span></li>
        </ol>
        <div className="row mt-16">
          <Button variant="ghost" onClick={() => app.setPage("hosts")}>{l("去宿主页", "Open Hosts")}</Button>
          <Button variant="ghost" onClick={() => app.setPage("models")}>{l("去模型页", "Open Models")}</Button>
          <Button variant="ghost" onClick={() => app.setPage("learning")}>{l("去学习页", "Open Learning")}</Button>
        </div>
      </Section>

      <Section title={l("如何获取 API Key", "How to get an API key")} subtitle={l("harness2-morphd 不代理、不转售模型调用；你直接与服务商结算。", "harness2-morphd does not proxy or resell model calls; you settle directly with the provider.")}>
        <div className="grid-3">
          {KEY_HELP.map((item) => (
            <div className="fact" key={item.label}>
              <strong>{item.label}</strong>
              <ExternalLink href={item.url}>{l("官方入口", "Official page")}</ExternalLink>
            </div>
          ))}
        </div>
        <InfoNote icon="key">
          {l(
            "Key 只保存在本机 Keychain 或加密保险库；不会写入配置文件、日志、导出或浏览器存储。界面只显示尾号。",
            "Keys stay in the local keychain or encrypted vault; never in config, logs, exports, or browser storage. The UI shows only the last digits.",
          )}
        </InfoNote>
      </Section>

      <Section title={l("宿主与生命周期", "Hosts and lifecycle")} subtitle={l("harness2-morphd 不常驻后台，也没有开机自启。", "harness2-morphd does not stay resident and has no startup entry.")}>
        <div className="stack">
          <InfoNote icon="host">
            {l(
              "UI 与引擎随宿主启动/退出。宿主退出后页面显示已断开，不留下进程、端口、计划任务或孤儿托盘。",
              "The UI and engine follow the host. After exit the page disconnects and leaves no process, port, scheduled task, or orphan tray.",
            )}
          </InfoNote>
          <InfoNote tone="warn" icon="terminal">
            {l(
              "当前 dsh/Claude/Codex 通过 wrapper 或 SessionEnd hook 绑定生命周期；没有原生 hook 时，会话结束分析会在下次宿主启动时从队列续跑。",
              "dsh/Claude/Codex bind lifecycle through wrappers or SessionEnd hooks. Without a native hook, queued analysis resumes on the next host launch.",
            )}
          </InfoNote>
          <div className="grid-3">
            <div className="fact"><strong>dsh</strong>wrapper / 原生插件（按能力）</div>
            <div className="fact"><strong>Claude Code</strong>SessionEnd hook / wrapper</div>
            <div className="fact"><strong>Codex</strong>wrapper；原生 sandbox，不使用 bypass</div>
          </div>
        </div>
      </Section>

      <Section title={l("撤销与恢复", "Undo and restore")} subtitle={l("任何应用都有快照；默认撤销窗口 7 天。", "Every apply has a snapshot; the default undo window is 7 days.")}>
        <ol className="step-list">
          <li><span className="badge badge-outline">1</span><span>{l("在“历史”中找到已应用的建议，点击撤销，1 分钟内恢复应用前文件。", "In History, open an applied suggestion and revert; the pre-apply files return within a minute.")}</span></li>
          <li><span className="badge badge-outline">2</span><span>{l("如果文件被你手动改过，harness2-morphd 会停止撤销并提示冲突，而不是覆盖。", "If you edited a file manually, harness2-morphd stops instead of overwriting it.")}</span></li>
          <li><span className="badge badge-outline">3</span><span>{l("要回到最初状态，在“宿主”页使用“恢复原始 harness”。", "To return to the earliest state, use Restore original harness on Hosts.")}</span></li>
        </ol>
      </Section>

      <Section title={l("故障排查", "Troubleshooting")}>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{l("现象", "Symptom")}</th>
                <th>{l("你可以做什么", "What you can do")}</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>{l("页面显示已断开", "Page says disconnected")}</td>
                <td>{l("重新打开宿主，再从宿主菜单/启动地址打开 harness2-morphd；这是生命周期绑定，不是网络问题。", "Reopen the host and launch morphd again. This is lifecycle binding, not a network issue.")}</td>
              </tr>
              <tr>
                <td>{l("没有找到 Harness", "No Harness found")}</td>
                <td>{l("在宿主页手动选择安装目录；harness2-morphd 不会扫描全盘，也不会自动安装宿主。", "Choose the install folder on Hosts. harness2-morphd never scans the whole disk or installs a host.")}</td>
              </tr>
              <tr>
                <td>{l("模型 Key 无效", "Invalid model key")}</td>
                <td>{l("到模型页重新保存 Key 并测试连接；修复前不会启动分析。", "Re-save the key on Models and test. No analysis starts before the fix.")}</td>
              </tr>
              <tr>
                <td>{l("预算用完", "Budget exhausted")}</td>
                <td>{l("在预算页调高上限，或等下月；harness2-morphd 不会先花后通知。", "Raise the limit on Budget or wait. harness2-morphd never spends first.")}</td>
              </tr>
              <tr>
                <td>{l("建议被安全拒绝", "Suggestion rejected")}</td>
                <td>{l("查看历史中的拒绝原因；涉及权限/hooks/settings/MCP 的候选永久禁止。", "See the reason in History; permission/hooks/settings/MCP candidates are permanently forbidden.")}</td>
              </tr>
              <tr>
                <td>{l("托盘不出现", "Tray missing")}</td>
                <td>{l("浏览器路径不提供系统托盘；无桌面会话时降级为浏览器 UI。", "The browser path has no system tray; without a desktop session it degrades to the browser UI.")}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div className="row mt-16">
          <Button variant="ghost" onClick={() => app.setPage("diagnostics")}>{l("打开诊断页", "Open Diagnostics")}</Button>
        </div>
      </Section>

      <Section title={l("术语表", "Glossary")} subtitle={l("新手模式默认开启；这些词不会作为主文案出现。", "Beginner mode is on by default; these terms never appear as primary copy.")}>
        <div className="list">
          {GLOSSARY.map((item) => (
            <div className="list-item" key={item.name}>
              <div className="list-main">
                <span className="list-title">{item.name}</span>
                <span className="sub">{lang === "zh" ? item.zh : item.en}</span>
              </div>
              <Term name={item.name}>{l("解释", "Explain")}</Term>
            </div>
          ))}
        </div>
      </Section>

      <Section title={l("隐私政策摘要", "Privacy summary")} subtitle={l("完整条款可由发布方在 docs/legal 中提供；此页给出产品实际行为。", "The publisher can provide full legal text under docs/legal; this page reflects actual behavior.")}>
        <ul className="step-list">
          <li><span>•</span><span>{l("默认只在本机处理；不上传遥测、不上传会话原文。", "Processing is local by default; no telemetry or full-session upload.")}</span></li>
          <li><span>•</span><span>{l("只有你选择的模型服务商能看到脱敏后的调用内容；可预览、可删除。", "Only your chosen provider sees redacted request content; you can preview and delete.")}</span></li>
          <li><span>•</span><span>{l("Key 保存在 Keychain 或本地加密保险库；不出现在日志/导出。", "Keys live in the keychain or encrypted vault and never appear in logs or exports.")}</span></li>
          <li><span>•</span><span>{l("harness2-morphd 不使用你的数据训练模型，不跨宿主/设备共享经验。", "harness2-morphd does not train on your data or share experience across hosts/devices.")}</span></li>
          <li><span>•</span><span>{l("数据默认保留 30 天，可改为 7/90 天或永久，并可立即删除。", "Data is kept for 30 days by default; choose 7/90/forever and delete at any time.")}</span></li>
        </ul>
      </Section>

      <Section title={l("服务条款摘要", "Terms summary")}>
        <ul className="step-list">
          <li><span>•</span><span>{l("BYOK：模型调用由你的服务商计费，harness2-morphd 不转售、不代理，也不为模型效果作担保。", "BYOK: your provider bills model calls; harness2-morphd does not resell, proxy, or guarantee model outcomes.")}</span></li>
          <li><span>•</span><span>{l("自动改动默认关闭；开启后仍受安全扫描、预算、试用与自动回滚约束。", "Auto-apply is off by default; when on, safety scans, budgets, trials, and rollback still apply.")}</span></li>
          <li><span>•</span><span>{l("不得使用本产品绕过组织的数据合规、权限或安全策略。", "Do not use this product to bypass organizational data, permission, or security policies.")}</span></li>
          <li><span>•</span><span>{l("代码以 Apache-2.0 发布；上游 Harness² 保留原版权与 NOTICE。", "Code is Apache-2.0; upstream Harness² keeps its copyright and NOTICE.")}</span></li>
        </ul>
      </Section>

      <Section title={l("关于", "About")}>
        <div className="grid-2">
          <div className="stack-sm">
            <span className="small strong">harness2-morphd</span>
            <span className="sub">{l("本地优先的 Agent harness 自动优化。", "Local-first automatic agent harness improvement.")}</span>
            <Badge variant="accent">{app.status?.app_version || "0.5.0"}</Badge>
          </div>
          <div className="stack-sm">
            <span className="small strong">{l("视觉", "Visual")}</span>
            <span className="sub">{l("仅 NVIDIA 绿 #76B900、纯白、纯黑；无灰、无渐变、无半透明；圆角 4/6/8/10px。", "Only NVIDIA green #76B900, pure white, pure black; no grey, gradients, or transparency; radii 4/6/8/10.")}</span>
          </div>
        </div>
        <InfoNote icon="shield">
          {l(
            "本产品仅使用绿色色值，不使用 NVIDIA Logo、商标或暗示官方合作。",
            "This product uses only the green color value, not the NVIDIA logo, trademarks, or any implied affiliation.",
          )}
        </InfoNote>
      </Section>
    </>
  );
}
