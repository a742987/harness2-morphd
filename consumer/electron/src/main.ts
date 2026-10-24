import {
  BrowserWindow,
  Menu,
  Notification,
  app,
  dialog,
  ipcMain,
  nativeTheme,
  session,
  shell,
} from "electron";
import { join } from "node:path";
import { unlinkSync } from "node:fs";
import type { ChildProcess } from "node:child_process";
import { fileURLToPath } from "node:url";
import {
  api,
  findEngine,
  homeDir,
  readInstall,
  readServer,
  requestStopLearning,
  spawnEngine,
  spawnHostWrapper,
  waitForServer,
  type InstallHost,
  type ServerInfo,
} from "./engine.js";
import { TrayController } from "./tray.js";

const __dirname = fileURLToPath(new URL(".", import.meta.url));
const home = homeDir();
const configOnly = process.argv.includes("--config-only");
const launchHost = process.argv.includes("--launch-host");
const requestedHost = (() => {
  const index = process.argv.indexOf("--host");
  return index >= 0 ? process.argv[index + 1] : "";
})();

let mainWindow: BrowserWindow | null = null;
let tray: TrayController | null = null;
let server: ServerInfo | null = null;
let child: ChildProcess | null = null;
let childOwnsHost = false;
let quitting = false;
let hintShown = false;
let closeToTray = true;
let startMinimized = false;
let pollTimer: NodeJS.Timeout | null = null;
let notificationTimer: NodeJS.Timeout | null = null;
let notificationSeeded = false;
const candidateStates = new Map<string, string>();

interface NotificationSettings {
  cadence?: {
    system_notifications?: boolean;
    notification_sound?: boolean;
    quiet_hours?: [string, string] | null;
  };
}

interface NotificationStatus {
  learning?: { paused?: boolean; vacation?: boolean; focus?: boolean };
}

interface NotificationCandidate {
  id?: string;
  state?: string;
  automatic?: boolean;
  confidence?: string;
  presented?: boolean;
  present_after?: string;
}

const ALLOWED_EXTERNAL = new Set([
  "platform.openai.com",
  "console.anthropic.com",
  "aistudio.google.com",
  "platform.deepseek.com",
  "openrouter.ai",
  "ollama.com",
  "github.com",
]);

const PICK_FOLDER_KINDS = ["install", "config", "project"] as const;
type PickFolderKind = (typeof PICK_FOLDER_KINDS)[number];
const PICK_FOLDER_TITLES: Record<PickFolderKind, string> = {
  install: "选择 Harness 安装目录 / Choose the Harness install folder",
  config: "选择 Harness 配置目录 / Choose the Harness config folder",
  project: "选择工作区或项目目录 / Choose a workspace or project folder",
};

function isPickFolderKind(value: unknown): value is PickFolderKind {
  return typeof value === "string" && (PICK_FOLDER_KINDS as readonly string[]).includes(value);
}

function iconPath(): string {
  return join(app.getAppPath(), "build", "icon.png");
}

function preloadPath(): string {
  return join(__dirname, "preload.cjs");
}

function localOrigin(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (parsed.hostname === "127.0.0.1" || parsed.hostname === "localhost" || parsed.hostname === "::1") {
      return parsed.origin;
    }
  } catch {
    return null;
  }
  return null;
}

function validSender(event: Electron.IpcMainInvokeEvent): boolean {
  if (!mainWindow || event.sender.id !== mainWindow.webContents.id) return false;
  const frame = event.senderFrame;
  if (!frame) return false;
  if (!server) return false;
  const frameOrigin = localOrigin(frame.url);
  const expected = localOrigin(server.base_url);
  // Both sides must be a concrete loopback origin; two nulls must never compare equal.
  return Boolean(frameOrigin && expected && frameOrigin === expected);
}

function inQuietHours(window: [string, string] | null | undefined, now = new Date()): boolean {
  if (!Array.isArray(window) || window.length !== 2) return false;
  const minutes = now.getHours() * 60 + now.getMinutes();
  const parse = (value: string): number => {
    const [hour, minute] = String(value).split(":").map(Number);
    return Number.isFinite(hour) && Number.isFinite(minute) ? hour * 60 + minute : NaN;
  };
  const start = parse(window[0]);
  const end = parse(window[1]);
  if (!Number.isFinite(start) || !Number.isFinite(end)) return false;
  return start <= end ? minutes >= start && minutes < end : minutes >= start || minutes < end;
}

function showSystemNotification(body: string, sound = false): void {
  if (!Notification.isSupported()) return;
  // Never include session content, filenames, diffs or keys.
  new Notification({ title: "harness2-morphd", body, silent: !sound }).show();
}

async function pollSuggestionNotifications(): Promise<void> {
  if (!server) return;
  try {
    const [settings, status, payload] = await Promise.all([
      api(server, "GET", "/api/settings") as Promise<NotificationSettings>,
      api(server, "GET", "/api/status") as Promise<NotificationStatus>,
      api(server, "GET", "/api/candidates") as unknown as Promise<NotificationCandidate[]>,
    ]);
    const candidates = (Array.isArray(payload) ? payload : []).filter((item) => item.id);
    const current = new Map<string, string>();
    const sound = Boolean(settings.cadence?.notification_sound);
    const eligible =
      Boolean(settings.cadence?.system_notifications) &&
      !status.learning?.paused &&
      !status.learning?.vacation &&
      !status.learning?.focus &&
      !inQuietHours(settings.cadence?.quiet_hours);

    for (const candidate of candidates) {
      const id = String(candidate.id);
      const state = String(candidate.state || "");
      const previous = candidateStates.get(id);
      current.set(id, state);
      if (!notificationSeeded) continue;
      const isNew = previous === undefined;
      if (!isNew && previous === state) continue;
      if (!eligible) continue;
      if (
        candidate.automatic &&
        candidate.presented &&
        (state === "applied" || state === "trial") &&
        (isNew || previous === "needs_apply")
      ) {
        showSystemNotification("已自动应用一条改进；可在历史中查看或撤销。", sound);
      } else if (
        state === "needs_apply" &&
        candidate.presented &&
        candidate.confidence === "high" &&
        (!candidate.present_after ||
          new Date(candidate.present_after).getTime() <= Date.now())
      ) {
        showSystemNotification(
          "有一条高置信度改进建议已准备好；打开 harness2-morphd 查看。",
          sound,
        );
      }
    }
    candidateStates.clear();
    for (const [id, state] of current) candidateStates.set(id, state);
    notificationSeeded = true;
  } catch {
    /* local service unavailable; the tray/window already show the disconnect */
  }
}

function startSuggestionNotifications(): void {
  if (notificationTimer) clearInterval(notificationTimer);
  notificationSeeded = false;
  candidateStates.clear();
  void pollSuggestionNotifications();
  notificationTimer = setInterval(() => void pollSuggestionNotifications(), 15000);
}

function stopSuggestionNotifications(): void {
  if (notificationTimer) clearInterval(notificationTimer);
  notificationTimer = null;
}

function createWindow(info: ServerInfo): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 900,
    minWidth: 900,
    minHeight: 620,
    backgroundColor: nativeTheme.shouldUseDarkColors ? "#000000" : "#FFFFFF",
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: preloadPath(),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      spellcheck: false,
      devTools: !app.isPackaged,
    },
  });
  const win = mainWindow;
  win.once("ready-to-show", () => {
    if (!startMinimized || configOnly) win.show();
  });
  win.on("close", (event) => {
    if (!quitting && closeToTray && tray?.available()) {
      event.preventDefault();
      win.hide();
      if (!hintShown) {
        hintShown = true;
        if (Notification.isSupported()) {
          new Notification({
            title: "harness2-morphd 仍在宿主中运行",
            body: "已隐藏到托盘；宿主退出后自动关闭。点击托盘图标可重新打开。",
          }).show();
        }
      }
    } else if (!quitting) {
      app.quit();
    }
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    try {
      const parsed = new URL(url);
      if (parsed.protocol === "https:" && ALLOWED_EXTERNAL.has(parsed.hostname)) {
        void shell.openExternal(url);
      }
    } catch {
      /* ignore malformed URLs */
    }
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    if (localOrigin(url) !== localOrigin(info.base_url)) event.preventDefault();
  });
  win.webContents.on("will-attach-webview", (event) => event.preventDefault());
  void win.loadURL(info.url);
}

function setupSessionSecurity(): void {
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  session.defaultSession.setPermissionCheckHandler(() => false);
  session.defaultSession.webRequest.onBeforeRequest((details, callback) => {
    try {
      const parsed = new URL(details.url);
      if (["http:", "https:"].includes(parsed.protocol) && !localOrigin(details.url)) {
        callback({ cancel: true });
        return;
      }
    } catch {
      /* non-URL schemes from Electron internals */
    }
    callback({});
  });
  app.on("web-contents-created", (_event, contents) => {
    contents.on("will-attach-webview", (event) => event.preventDefault());
    contents.setWindowOpenHandler(({ url }) => {
      try {
        const parsed = new URL(url);
        if (parsed.protocol === "https:" && ALLOWED_EXTERNAL.has(parsed.hostname)) {
          void shell.openExternal(url);
        }
      } catch {
        /* ignore */
      }
      return { action: "deny" };
    });
  });
}

function chooseHost(): InstallHost | null {
  const install = readInstall(home);
  const hosts = install?.hosts || [];
  if (requestedHost) return hosts.find((item) => item.host === requestedHost) || null;
  return hosts.find((item) => item.status === "adapted" || item.status === "beta") || null;
}

async function ensureServer(): Promise<{ info: ServerInfo; mode: "config" | "host" | "attach" }> {
  const host = chooseHost();
  const existing = readServer(home);
  if (existing) {
    try {
      await api(existing, "GET", "/api/status");
      return { info: existing, mode: configOnly || !host ? "config" : "attach" };
    } catch {
      try {
        unlinkSync(join(home, "runtime", "server.json"));
      } catch {
        /* stale file already gone */
      }
    }
  }
  if (!configOnly && launchHost) {
    if (host) {
      child = spawnHostWrapper(host, home);
      childOwnsHost = true;
      child.on("exit", () => {
        if (!quitting) {
          server = null;
          tray?.stop();
          app.quit();
        }
      });
      const info = await waitForServer(home);
      return { info, mode: "host" };
    }
  }
  try {
    findEngine();
  } catch (error) {
    throw new Error(
      `${(error as Error).message}\n\n没有适配宿主，也没有可用的引擎；将以仅配置模式打开。`,
    );
  }
  child = spawnEngine(["serve", "--config-only", "--home", home], home);
  child.on("exit", () => {
    if (!quitting && server) {
      server = null;
      tray?.stop();
      app.quit();
    }
  });
  const info = await waitForServer(home);
  return { info, mode: "config" };
}

function wireIpc(): void {
  ipcMain.handle("morphd:status", async (event) => {
    if (!validSender(event) || !server) throw new Error("forbidden");
    return api(server, "GET", "/api/status");
  });
  ipcMain.handle("morphd:show", (event) => {
    if (!validSender(event)) throw new Error("forbidden");
    mainWindow?.show();
    mainWindow?.focus();
  });
  ipcMain.handle("morphd:open-settings", (event) => {
    if (!validSender(event)) throw new Error("forbidden");
    mainWindow?.show();
    void mainWindow?.webContents.executeJavaScript("window.location.hash = '#/learning'");
  });
  ipcMain.handle("morphd:pick-folder", async (event, kind: unknown) => {
    if (!validSender(event) || !mainWindow) throw new Error("forbidden");
    if (!isPickFolderKind(kind)) throw new Error("invalid folder kind");
    const result = await dialog.showOpenDialog(mainWindow, {
      title: PICK_FOLDER_TITLES[kind],
      properties: ["openDirectory"],
    });
    if (result.canceled || result.filePaths.length === 0) return { path: "" };
    return { path: result.filePaths[0] };
  });
  ipcMain.handle("morphd:pause", async (event, until: unknown) => {
    if (!validSender(event) || !server || typeof until !== "string") throw new Error("forbidden");
    return api(server, "POST", "/api/learning/pause", { until });
  });
  ipcMain.handle("morphd:incognito", async (event, active: unknown) => {
    if (!validSender(event) || !server || typeof active !== "boolean") throw new Error("forbidden");
    return api(server, "POST", "/api/sessions/incognito", { active });
  });
  ipcMain.handle("morphd:apply-candidate", async (event, id: unknown) => {
    if (!validSender(event) || !server || typeof id !== "string") throw new Error("forbidden");
    return api(server, "POST", `/api/candidates/${encodeURIComponent(id)}/apply`, { confirm: true });
  });
  ipcMain.handle("morphd:revert-candidate", async (event, id: unknown) => {
    if (!validSender(event) || !server || typeof id !== "string") throw new Error("forbidden");
    return api(server, "POST", `/api/candidates/${encodeURIComponent(id)}/revert`, { confirm: true });
  });
  ipcMain.handle("morphd:quit-shell", (event) => {
    if (!validSender(event)) throw new Error("forbidden");
    app.quit();
  });
}

function startPolling(): void {
  if (!server) return;
  pollTimer = setInterval(async () => {
    if (!server) return;
    try {
      const info = readServer(home);
      if (!info) {
        tray?.stop();
        if (!quitting) app.quit();
        return;
      }
      await api(info, "GET", "/api/status");
      server = info;
      tray?.updateInfo(info);
    } catch {
      // A dead or hijacked server must not leave an orphan window/tray. Verify once more
      // through process liveness before exiting.
      if (!readServer(home)) {
        tray?.stop();
        if (!quitting) app.quit();
      }
    }
  }, 5000);
}

async function confirmStopLearning(): Promise<void> {
  if (!mainWindow) return;
  const result = await dialog.showMessageBox(mainWindow, {
    type: "warning",
    buttons: ["取消", "停止学习并退出"],
    defaultId: 0,
    cancelId: 0,
    title: "停止学习并退出",
    message: "停止 harness2-morphd 的学习与界面，但宿主会继续运行。",
    detail: "不会关闭宿主，也不会撤销已应用的改动。之后可从宿主菜单重新打开。",
  });
  if (result.response === 1) {
    requestStopLearning(home);
    setTimeout(() => app.quit(), 300);
  }
}

async function boot(): Promise<void> {
  setupSessionSecurity();
  wireIpc();
  let info: ServerInfo;
  let mode: "config" | "host" | "attach";
  try {
    ({ info, mode } = await ensureServer());
  } catch (error) {
    console.error("harness2-morphd boot failed:", error);
    dialog.showErrorBox("harness2-morphd 无法启动", (error as Error).message);
    app.quit();
    return;
  }
  server = info;
  createWindow(info);
  let trayEnabled = true;
  let badgeEnabled = false;
  try {
    const settings = (await api(info, "GET", "/api/settings")) as {
      cadence?: { tray_enabled?: boolean; close_window?: string; suggestion_badge?: boolean };
    };
    trayEnabled = settings.cadence?.tray_enabled !== false;
    closeToTray = settings.cadence?.close_window !== "exit";
    badgeEnabled = settings.cadence?.suggestion_badge === true;
  } catch {
    trayEnabled = true;
    closeToTray = true;
  }
  if (mode !== "config" && trayEnabled) {
    tray = new TrayController(
      info,
      {
        showWindow: () => {
          mainWindow?.show();
          mainWindow?.focus();
        },
        settings: () => {
          mainWindow?.show();
          void mainWindow?.webContents.executeJavaScript("window.location.hash = '#/learning'");
        },
        quitShell: () => app.quit(),
        stopLearningAndQuit: () => void confirmStopLearning(),
      },
      iconPath(),
      join(app.getAppPath(), "build", "icon-badge.png"),
    );
    tray.setBadgeEnabled(badgeEnabled);
    if (!tray.start()) {
      tray = null;
      if (mainWindow) {
        mainWindow.webContents.once("did-finish-load", () => {
          void mainWindow?.webContents.executeJavaScript(
            "document.documentElement.dataset.noTray = '1'",
          );
        });
      }
    }
  }
  if (mode !== "config") startSuggestionNotifications();
  startPolling();
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    }
  });
  app.whenReady().then(boot);
  app.on("before-quit", () => {
    quitting = true;
    if (pollTimer) clearInterval(pollTimer);
    stopSuggestionNotifications();
    tray?.stop();
    // A host wrapper owns its host and engine; never kill the user's host here.
    if (child && !child.killed && !childOwnsHost) {
      child.kill();
    }
  });
  app.on("window-all-closed", () => {
    if (!tray?.available()) app.quit();
  });
}

Menu.setApplicationMenu(null);
