import type { ReactNode } from "react";
import { api } from "../api";
import { useL, useLang } from "../i18n";
import { useApp } from "../state";
import type { Page } from "../types";
import { Icon, type IconName } from "./Icon";
import { Button, IconButton } from "./UI";

const NAV: { page: Page; icon: IconName; zh: string; en: string }[] = [
  { page: "home", icon: "home", zh: "首页", en: "Home" },
  { page: "hosts", icon: "host", zh: "宿主", en: "Hosts" },
  { page: "models", icon: "model", zh: "模型", en: "Models" },
  { page: "learning", icon: "learn", zh: "学习", en: "Learning" },
  { page: "budget", icon: "budget", zh: "预算", en: "Budget" },
  { page: "history", icon: "history", zh: "历史", en: "History" },
  { page: "privacy", icon: "privacy", zh: "隐私", en: "Privacy" },
  { page: "diagnostics", icon: "diagnostics", zh: "诊断", en: "Diagnostics" },
  { page: "help", icon: "help", zh: "帮助", en: "Help" },
  { page: "advanced", icon: "advanced", zh: "高级", en: "Advanced" },
];

export function Layout({ children }: { children: ReactNode }) {
  const app = useApp();
  const l = useL();
  const lang = useLang();
  const pending = app.candidates.filter((item) => item.state === "needs_apply").length;
  const status = app.status;
  const learning = status?.learning;
  const connectedHost = status?.hosts.find((host) => host.connected) || status?.hosts[0];
  const stateLabel = !status
    ? l("正在连接", "Connecting")
    : learning?.paused
      ? l("已暂停", "Paused")
      : learning?.incognito_next
        ? l("下一次隐身", "Next is incognito")
        : status.hosts.length === 0
          ? l("未连接宿主", "No host")
          : l("学习中", "Learning");
  const quickTheme = () => {
    if (!app.settings) return;
    const current = app.settings.ui.theme;
    const next = current === "light" ? "dark" : current === "dark" ? "system" : "light";
    void app.saveSettings({ ui: { ...app.settings.ui, theme: next } });
  };
  const quickLang = () => {
    if (!app.settings) return;
    const next = app.settings.ui.language === "zh" ? "en" : app.settings.ui.language === "en" ? "system" : "zh";
    void app.saveSettings({ ui: { ...app.settings.ui, language: next } });
  };
  const pauseResume = async () => {
    if (!learning?.paused) {
      await app.saveSettings({ enabled: true });
      try {
        await api.pause("1d");
        await app.refreshAll(true);
        app.toast("ok", "已暂停 1 天", "Paused for 1 day");
      } catch (error) {
        const apiError = app.handleError(error);
        if (apiError) app.toast("warn", apiError.message, apiError.message);
      }
    } else {
      try {
        await api.resume();
        await app.refreshAll(true);
        app.toast("ok", "已恢复自动学习", "Learning resumed");
      } catch (error) {
        const apiError = app.handleError(error);
        if (apiError) app.toast("warn", apiError.message, apiError.message);
      }
    }
  };

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        {l("跳到主要内容", "Skip to main content")}
      </a>
      <aside className="sidebar" aria-label={l("主导航", "Main navigation")}>
        <div className="brand" aria-label="harness2-morphd">
          <span className="brand-mark" aria-hidden="true">
            m
          </span>
          <span className="brand-text">
            <strong>harness2-morphd</strong>
            <span>{l("安静地变好", "quietly better")}</span>
          </span>
        </div>
        <nav className="nav">
          {NAV.map((item) => (
            <button
              key={item.page}
              type="button"
              className={`nav-item ${app.page === item.page ? "is-active" : ""}`}
              aria-current={app.page === item.page ? "page" : undefined}
              onClick={() => app.setPage(item.page)}
            >
              <Icon name={item.icon} size={19} />
              <span>{lang === "zh" ? item.zh : item.en}</span>
              {item.page === "history" && pending > 0 ? (
                <span className="nav-count" aria-label={l(`${pending} 条待看`, `${pending} in inbox`)}>
                  {pending}
                </span>
              ) : null}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="sidebar-status">
            <span className={`status-shape ${learning?.paused ? "status-warn" : status?.hosts.length ? "status-ok" : "status-idle"}`} aria-hidden="true" />
            <span>{stateLabel}</span>
          </div>
          <span className="sub">
            {connectedHost
              ? `${connectedHost.label} ${connectedHost.version}`
              : l("还没有适配宿主", "No host adapted yet")}
          </span>
          <div className="sidebar-actions">
            <Button size="sm" variant="ghost" icon={learning?.paused ? "play" : "pause"} onClick={pauseResume}>
              {learning?.paused ? l("恢复", "Resume") : l("暂停 1 天", "Pause 1d")}
            </Button>
            <IconButton
              icon="sun"
              label={l("切换主题", "Toggle theme")}
              onClick={quickTheme}
            />
            <IconButton
              icon="help"
              label={l("切换语言", "Toggle language")}
              onClick={quickLang}
            />
          </div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true">
              m
            </span>
            <span className="brand-text">
              <strong>harness2-morphd</strong>
              <span>{stateLabel}</span>
            </span>
          </div>
          <div className="row">
            <IconButton icon="sun" label={l("切换主题", "Toggle theme")} onClick={quickTheme} />
          </div>
        </header>
        <main className="content" id="main-content" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
