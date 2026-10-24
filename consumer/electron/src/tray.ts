import {
  Menu,
  Tray,
  nativeImage,
  type MenuItemConstructorOptions,
} from "electron";
import { api, type ServerInfo } from "./engine.js";

export interface TrayHooks {
  showWindow: () => void;
  quitShell: () => void;
  stopLearningAndQuit: () => void;
  settings: () => void;
}

interface StatusPayload {
  learning?: {
    paused?: boolean;
    incognito_next?: boolean;
    active_sessions?: { session_id?: string; host?: string }[];
  };
  budget?: { monthly?: { spent?: number; limit?: number } };
  hosts?: { label?: string; connected?: boolean }[];
  tray?: { available?: boolean; status?: string };
}

export class TrayController {
  private tray: Tray | null = null;
  private timer: NodeJS.Timeout | null = null;
  private status: StatusPayload = {};
  private info: ServerInfo;
  private badgeEnabled = false;
  private badgeShown = false;

  constructor(
    info: ServerInfo,
    private readonly hooks: TrayHooks,
    private readonly iconPath: string,
    private readonly badgeIconPath = "",
  ) {
    this.info = info;
  }

  setBadgeEnabled(enabled: boolean): void {
    this.badgeEnabled = enabled;
    if (!enabled) this.setBadge(false);
  }

  private setBadge(active: boolean): void {
    if (!this.tray || this.badgeShown === active) return;
    this.badgeShown = active;
    try {
      if (active && this.badgeIconPath) {
        const badge = nativeImage.createFromPath(this.badgeIconPath);
        if (!badge.isEmpty()) {
          this.tray.setImage(badge);
          return;
        }
      }
      this.tray.setImage(nativeImage.createFromPath(this.iconPath));
    } catch {
      /* keep the current icon */
    }
  }

  available(): boolean {
    return this.tray !== null;
  }

  start(): boolean {
    try {
      const image = nativeImage.createFromPath(this.iconPath);
      this.tray = new Tray(image.isEmpty() ? nativeImage.createEmpty() : image);
      this.tray.setToolTip("harness2-morphd");
      this.tray.on("click", () => this.hooks.showWindow());
      this.refresh();
      this.timer = setInterval(() => void this.refresh(), 5000);
      return true;
    } catch {
      this.tray = null;
      return false;
    }
  }

  updateInfo(info: ServerInfo): void {
    this.info = info;
  }

  async refresh(): Promise<void> {
    try {
      this.status = (await api(this.info, "GET", "/api/status")) as StatusPayload;
      if (this.badgeEnabled) {
        try {
          const candidates = (await api(this.info, "GET", "/api/candidates?state=needs_apply")) as unknown as {
            id?: string;
            presented?: boolean;
          }[];
          this.setBadge(
            Array.isArray(candidates) &&
              candidates.some((candidate) => candidate.id && candidate.presented),
          );
        } catch {
          this.setBadge(false);
        }
      }
      this.render();
    } catch {
      this.stop();
    }
  }

  private render(): void {
    if (!this.tray) return;
    const paused = Boolean(this.status.learning?.paused);
    const incognito = Boolean(this.status.learning?.incognito_next);
    const spent = Number(this.status.budget?.monthly?.spent || 0).toFixed(2);
    const limit = Number(this.status.budget?.monthly?.limit || 0).toFixed(2);
    const host = this.status.hosts?.find((item) => item.connected) || this.status.hosts?.[0];
    const active = this.status.learning?.active_sessions?.[0];
    const trayStatus = this.status.tray?.status || (host?.connected ? "idle" : "not_connected");
    const labels: Record<string, string> = {
      learning: `学习中${host?.label ? ` · ${host.label}` : ""}`,
      paused: "已暂停",
      incognito: "本次隐身",
      budget_exhausted: "预算用完",
      needs_setup: "需要配置模型",
      needs_attention: "需要处理",
      model_error: "模型错误",
      not_connected: "未连接宿主",
      idle: host?.connected ? `待命${host?.label ? ` · ${host.label}` : ""}` : "未连接宿主",
    };
    const statusText = paused && trayStatus !== "paused"
      ? "已暂停"
      : labels[trayStatus] || "未连接宿主";
    this.tray.setToolTip(`harness2-morphd · ${statusText} · 本月 $${spent} / $${limit}`);

    const hostItems: MenuItemConstructorOptions[] = (this.status.hosts || []).map((item) => ({
      label: `${item.label || "宿主"} · ${item.connected ? "已连接" : "未连接"}`,
      enabled: false,
    }));
    const incognitoItem: MenuItemConstructorOptions = active?.session_id
      ? {
          label: "本次会话立即隐身",
          click: () => void this.incognitoSession(active.session_id as string),
        }
      : {
          label: incognito ? "取消下一次隐身" : "下一次会话隐身",
          click: () => void this.incognito(!incognito),
        };

    const template: MenuItemConstructorOptions[] = [
      { label: `harness2-morphd · ${statusText}`, enabled: false },
      { label: "打开 harness2-morphd", click: () => this.hooks.showWindow() },
      {
        label: "状态",
        submenu: hostItems.length > 0 ? hostItems : [{ label: "未连接宿主", enabled: false }],
      },
      { label: "打开宿主（请在任务栏切换）", enabled: false },
      { label: "设置", click: () => this.hooks.settings() },
      { type: "separator" },
      { label: `本月花费 $${spent}`, enabled: false },
      { label: "暂停 1 小时", click: () => void this.pause("1h") },
      { label: "暂停 1 天", click: () => void this.pause("1d") },
      { label: "暂停 1 周", click: () => void this.pause("1w") },
      { label: "恢复学习", click: () => void this.resume() },
      incognitoItem,
      { type: "separator" },
      { label: "退出桌面壳（宿主继续运行）", click: () => this.hooks.quitShell() },
      { label: "停止学习并退出…", click: () => this.hooks.stopLearningAndQuit() },
    ];
    this.tray.setContextMenu(Menu.buildFromTemplate(template));
  }

  private async pause(until: string): Promise<void> {
    await api(this.info, "POST", "/api/learning/pause", { until });
    await this.refresh();
  }

  private async resume(): Promise<void> {
    await api(this.info, "POST", "/api/learning/resume", {});
    await this.refresh();
  }

  private async incognito(active: boolean): Promise<void> {
    await api(this.info, "POST", "/api/sessions/incognito", { active });
    await this.refresh();
  }

  private async incognitoSession(sessionId: string): Promise<void> {
    await api(this.info, "POST", "/api/sessions/incognito", {
      active: true,
      session_id: sessionId,
    });
    await this.refresh();
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    if (this.tray) {
      this.tray.destroy();
      this.tray = null;
    }
  }
}

