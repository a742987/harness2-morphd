import { createContext, useContext } from "react";
import type { Lang } from "./types";

export type L = (zh: string, en: string) => string;

interface LangValue {
  lang: Lang;
  l: L;
}

export const LangContext = createContext<LangValue>({
  lang: "zh",
  l: (zh) => zh,
});

export function useL(): L {
  return useContext(LangContext).l;
}

export function useLang(): Lang {
  return useContext(LangContext).lang;
}

export function resolveLang(setting: string, systemLang = navigator.language): Lang {
  if (setting === "zh" || setting === "en") return setting;
  return systemLang.toLowerCase().startsWith("zh") ? "zh" : "en";
}

export function formatMoney(usd: number | null | undefined, lang: Lang): string {
  if (usd === null || usd === undefined || Number.isNaN(usd)) {
    return lang === "zh" ? "未知" : "unknown";
  }
  const digits = Math.abs(usd) < 0.01 ? 4 : 2;
  return new Intl.NumberFormat(lang === "zh" ? "zh-CN" : "en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: digits,
  }).format(usd);
}

export function formatDate(value: string | null | undefined, lang: Lang): string {
  if (!value) return lang === "zh" ? "—" : "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(lang === "zh" ? "zh-CN" : "en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatDateOnly(value: string | null | undefined, lang: Lang): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(lang === "zh" ? "zh-CN" : "en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  }).format(date);
}

export function formatBytes(bytes: number, lang: Lang): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} ${lang === "zh" ? "MB" : "MB"}`;
}

export function timeAgo(value: string | null | undefined, lang: Lang): string {
  if (!value) return "—";
  const date = new Date(value).getTime();
  if (Number.isNaN(date)) return "—";
  const seconds = Math.round((Date.now() - date) / 1000);
  const zh = lang === "zh";
  if (seconds < 60) return zh ? "刚刚" : "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return zh ? `${minutes} 分钟前` : `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return zh ? `${hours} 小时前` : `${hours} h ago`;
  const days = Math.round(hours / 24);
  if (days < 14) return zh ? `${days} 天前` : `${days} d ago`;
  return formatDateOnly(value, lang);
}

export interface HumanError {
  what: string;
  why: string;
  action: string;
  money: string;
}

export function humanError(
  code: string,
  message: string,
  action: string,
  lang: Lang,
): HumanError {
  const zh = lang === "zh";
  const known: Record<string, Partial<HumanError>> = {
    DISCONNECTED: {
      what: zh ? "本地服务已断开。" : "The local service is disconnected.",
      why: zh ? "宿主或 harness2-morphd 已退出；页面不能再工作。" : "The host or harness2-morphd exited; this page cannot work.",
      action: zh ? "重新打开宿主，或从宿主菜单再次打开 harness2-morphd。" : "Reopen the host, then open morphd again.",
      money: zh ? "这不会产生费用。" : "This costs nothing.",
    },
    AUTH_REQUIRED: {
      what: zh ? "访问令牌无效或已过期。" : "The access token is missing or expired.",
      why: zh ? "页面不是从本次启动的本地地址打开。" : "This page was not opened from the current local address.",
      action: zh ? "使用启动时打印的本地地址重新打开。" : "Open the local address printed at startup.",
      money: zh ? "这不会产生费用。" : "This costs nothing.",
    },
    CSRF_INVALID: {
      what: zh ? "页面校验失败。" : "The page could not verify this request.",
      why: zh ? "页面可能过旧，或本地服务已重启。" : "The page may be stale, or the local service restarted.",
      action: zh ? "刷新页面后重试。" : "Refresh the page and try again.",
      money: zh ? "这不会产生费用。" : "This costs nothing.",
    },
    PROVIDER_AUTH_FAILED: {
      what: zh ? "模型 Key 无效、过期或没有权限。" : "The model key is invalid, expired, or unauthorized.",
      why: zh ? "服务商拒绝了鉴权；harness2-morphd 不会重试无效 Key。" : "The provider rejected authentication; harness2-morphd does not retry invalid keys.",
      action: action || (zh ? "到模型页更新 Key，再测试连接。" : "Update the key on the Models page and test again."),
      money: zh ? "失败前不会启动分析，也不会产生分析费用。" : "No analysis starts before the fix, so no analysis cost.",
    },
    PROVIDER_RATE_LIMITED: {
      what: zh ? "模型服务限流了。" : "The model service is rate limiting.",
      why: zh ? "短时间调用过多，或服务商临时限制。" : "Too many calls, or a temporary provider limit.",
      action: zh ? "稍后重试，或换一个 fallback 模型。" : "Retry later, or use a fallback model.",
      money: zh ? "失败调用可能按服务商账单计费；harness2-morphd 会记录实际用量。" : "Failed calls may be billed by the provider; harness2-morphd records actual usage.",
    },
    PROVIDER_UNAVAILABLE: {
      what: zh ? "无法连接模型服务。" : "The model service cannot be reached.",
      why: zh ? "网络、Base URL 或服务商故障。" : "Network, Base URL, or provider outage.",
      action: zh ? "检查网络与 Base URL；不会在后台反复重试。" : "Check the network and Base URL; harness2-morphd will not retry in the background.",
      money: zh ? "未发出请求时不会产生费用。" : "No cost if the request never reached the provider.",
    },
    PROVIDER_NOT_CONFIGURED: {
      what: zh ? "还没有可用的递归模型。" : "No recursive model is configured.",
      why: zh ? "模型角色未选择，或 Key 已被删除。" : "No model role is assigned, or its key was deleted.",
      action: zh ? "在模型页接入 BYOK 模型或本地 Ollama。" : "Add a BYOK model or local Ollama on the Models page.",
      money: zh ? "未配置模型时不会产生费用。" : "No model means no cost.",
    },
    BUDGET_EXCEEDED: {
      what: zh ? "预算上限阻止了这次操作。" : "A budget limit stopped this operation.",
      why: zh ? "按最坏情况估算会超过月度、单会话或单次分析预算。" : "The worst-case estimate exceeds the monthly, session, or job budget.",
      action: zh ? "调高预算或选择更便宜的模型。harness2-morphd 不会先花后通知。" : "Raise the budget or choose a cheaper model. harness2-morphd never spends first.",
      money: zh ? "被阻止的调用不会产生费用。" : "A blocked call costs nothing.",
    },
    BUDGET_UNKNOWN_PRICE: {
      what: zh ? "这个模型没有已确认的价格。" : "This model has no confirmed price.",
      why: zh ? "harness2-morphd 不会把未知价格按 0 计费。" : "harness2-morphd never silently treats an unknown price as zero.",
      action: zh ? "在模型页填写估算价格或明确确认。" : "Enter an estimate or confirm the price on the Models page.",
      money: zh ? "确认前不会启动分析，也不会产生费用。" : "No analysis starts before confirmation.",
    },
    HOST_NOT_FOUND: {
      what: zh ? "没有找到可用的 Harness。" : "No supported Harness was found.",
      why: zh ? "可执行文件不在 PATH 或常见目录。" : "The executable is not on PATH or in a common directory.",
      action: zh ? "手动选择 Harness 安装目录或可执行文件。" : "Choose the Harness install folder or executable manually.",
      money: zh ? "发现与适配不调用模型，不产生费用。" : "Discovery and adaptation make no model calls.",
    },
    HOST_RUNNING: {
      what: zh ? "宿主正在运行。" : "The host is running.",
      why: zh ? "运行中修改配置可能不生效，也可能损坏当前会话。" : "Changing config while running may not apply and can disrupt the session.",
      action: zh ? "关闭宿主后重试，或重启宿主使改动生效。" : "Close the host and retry, or restart it to apply changes.",
      money: zh ? "这不会产生费用。" : "This costs nothing.",
    },
    HOST_UNSUPPORTED: {
      what: zh ? "这个宿主或版本暂不支持。" : "This host or version is not supported.",
      why: zh ? "版本不在兼容矩阵，或缺少所需接口。" : "The version is outside the compatibility matrix or lacks required interfaces.",
      action: zh ? "查看兼容矩阵，或手动标记为 beta。" : "Check the compatibility matrix or mark it beta manually.",
      money: zh ? "不适配不会产生费用。" : "Unsupported adaptation costs nothing.",
    },
    ADAPT_FAILED: {
      what: zh ? "适配失败。" : "Adaptation failed.",
      why: message,
      action: zh ? "已自动回滚；可以重试或手动指定文件夹。" : "The change was rolled back; retry or choose a folder manually.",
      money: zh ? "这不会产生费用。" : "This costs nothing.",
    },
    APPLY_CONFLICT: {
      what: zh ? "目标文件已被手动修改。" : "The target file was edited manually.",
      why: zh ? "harness2-morphd 不会覆盖你的手工修改。" : "harness2-morphd never overwrites your manual edits.",
      action: zh ? "先备份或提交该文件，再应用/撤销。" : "Back up or commit the file, then apply or revert.",
      money: zh ? "这不会产生费用。" : "This costs nothing.",
    },
    CANDIDATE_STALE: {
      what: zh ? "这条建议基于旧版本的 harness。" : "This suggestion is based on an older harness.",
      why: zh ? "基线已变化，直接应用会覆盖其他改动。" : "The baseline changed; applying it could overwrite other edits.",
      action: zh ? "重新分析产生新建议，或查看历史对比。" : "Analyze again for a new suggestion, or compare history.",
      money: zh ? "这不会产生费用。" : "This costs nothing.",
    },
    CANDIDATE_UNSAFE: {
      what: zh ? "这条建议没有通过安全检查。" : "This suggestion failed the safety check.",
      why: message,
      action: zh ? "查看拒绝原因，或调整变更幅度后重新分析。" : "Review the reason, or change the appetite and analyze again.",
      money: zh ? "被拒绝的候选不会写入，也不会重试。" : "A rejected candidate is never written or retried.",
    },
    VAULT_LOCKED: {
      what: zh ? "本地加密保险库未解锁。" : "The encrypted local vault is locked.",
      why: zh ? "系统钥匙串不可用，需要用户口令。" : "The OS keychain is unavailable, so a user passphrase is required.",
      action: zh ? "输入保险库口令后重试。" : "Enter the vault passphrase and retry.",
      money: zh ? "这不会产生费用。" : "This costs nothing.",
    },
    JOB_CANCELED: {
      what: zh ? "任务已取消。" : "The job was canceled.",
      why: zh ? "你或宿主关闭了任务。" : "You or the host stopped it.",
      action: zh ? "重新打开宿主后可手动分析该会话。" : "After reopening the host, analyze the session manually.",
      money: zh ? "取消前已完成的调用会按实际用量记录。" : "Calls completed before cancellation are recorded.",
    },
    INVALID_REQUEST: {
      what: zh ? "这次操作没有完成。" : "This operation did not complete.",
      why: message,
      action: action || (zh ? "检查输入后重试。" : "Check the input and retry."),
      money: zh ? "这不会产生费用。" : "This costs nothing.",
    },
  };
  const base = known[code] || {};
  return {
    what: base.what || (zh ? "操作没有完成。" : "The operation did not complete."),
    why: base.why || message,
    action: base.action || action || (zh ? "重试或查看诊断页。" : "Retry or open Diagnostics."),
    money: base.money || (zh ? "不确定是否产生费用时，查看预算页的实际用量。" : "Check actual usage on the Budget page."),
  };
}

export const TERMS: Record<string, { zh: string; en: string }> = {
  harness: {
    zh: "Agent 的工作方式：提示、技能和脚本。harness2-morphd 会尝试改进这些文件。",
    en: "How the agent works: instructions, skills and scripts. harness2-morphd tries to improve these files.",
  },
  candidate: {
    zh: "一条改进建议。只有你点击“应用”后才会写入真实文件。",
    en: "An improvement suggestion. Nothing is written until you choose Apply.",
  },
  recursive: {
    zh: "让 Agent 自己尝试几种改法，再挑出更顺的一种。",
    en: "Let the agent try a few improvements and select the one that works best.",
  },
  incognito: {
    zh: "本次会话不记录、不分析、不产生费用。",
    en: "This session is not recorded, analyzed, or billed.",
  },
  quiet: {
    zh: "这段时间只把建议放进待看，不主动展示或通知。",
    en: "Suggestions go to the inbox without proactive display or notification.",
  },
  trial: {
    zh: "自动应用后的观察期；出现回归信号会自动回滚。",
    en: "A watch period after auto-apply; regressions trigger an automatic rollback.",
  },
  triage: {
    zh: "零成本判断：先看这次会话有没有值得分析的问题。",
    en: "A zero-cost check for whether the session is worth analyzing.",
  },
};
