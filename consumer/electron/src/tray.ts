import {
  Menu,
  Tray,
  nativeImage,
  type MenuItemConstructorOptions,
} from "electron";
import { api, type ServerInfo } from "./engine";

export interface TrayHooks {
  showWindow: () => void;
  quitShell: () => void;
  stopLearningAndQuit: () => void;
  settings: () => void;
}

interface StatusPayload {
  learning?: { paused?: boolean; incognito_next?: boolean };
  budget?: { monthly?: { spent?: number; limit?: number } };
  hosts?: { label?: string; connected?: boolean }[];
}

export class TrayController {
  private tray: Tray | null = null;
  private timer: NodeJS.Timeout | null = null;
  private status: StatusPayload = {};
  private info: ServerInfo;

  constructor(
    info: ServerInfo,
    private readonly hooks: TrayHooks,
    private readonly iconPath: string,
  ) {
    this.info = info;
  }

  available(): boolean {
    return this.tray !== null;
  }

  start(): boolean {
    try {
      const image = nativeImage.createFromPath(this.iconPath);
      if (process.platform === "darwin" && !image.isEmpty()) image.setTemplateImage(true);
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
    const statusText = paused
      ? "已暂停"
      : incognito
        ? "下一次会话隐身"
        : host?.connected
          ? `学习中 · ${host.label || ""}`
          : "未连接宿主";
    this.tray.setToolTip(`harness2-morphd · ${statusText} · 本月 $${spent} / $${limit}`);

    const template: MenuItemConstructorOptions[] = [
      { label: `harness2-morphd · ${statusText}`, enabled: false },
      { label: "打开 harness2-morphd", click: () => this.hooks.showWindow() },
      { label: "设置", click: () => this.hooks.settings() },
      { type: "separator" },
      { label: `本月花费 $${spent}`, enabled: false },
      { label: "暂停 1 小时", click: () => void this.pause("1h") },
      { label: "暂停 1 天", click: () => void this.pause("1d") },
      { label: "暂停 1 周", click: () => void this.pause("1w") },
      { label: "恢复学习", click: () => void this.resume() },
      { label: incognito ? "取消下一次隐身" : "下一次会话隐身", click: () => void this.incognito(!incognito) },
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

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    if (this.tray) {
      this.tray.destroy();
      this.tray = null;
    }
  }
}

