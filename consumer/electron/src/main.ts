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
let pollTimer: NodeJS.Timeout | null = null;

const ALLOWED_EXTERNAL = new Set([
  "platform.openai.com",
  "console.anthropic.com",
  "aistudio.google.com",
  "platform.deepseek.com",
  "openrouter.ai",
  "ollama.com",
  "github.com",
]);

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
  return localOrigin(frame.url) === localOrigin(server.base_url);
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
  win.once("ready-to-show", () => win.show());
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
      server = info;
      tray?.updateInfo(info);
    } catch {
      /* keep the window; the API layer handles disconnects */
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
  try {
    const settings = (await api(info, "GET", "/api/settings")) as {
      cadence?: { tray_enabled?: boolean; close_window?: string };
    };
    trayEnabled = settings.cadence?.tray_enabled !== false;
    closeToTray = settings.cadence?.close_window !== "exit";
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
    );
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
