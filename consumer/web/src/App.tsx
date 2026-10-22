import { Component, useEffect, useMemo, type ErrorInfo, type ReactNode } from "react";
import { Layout } from "./components/Layout";
import { Icon } from "./components/Icon";
import { Button, ToastHost } from "./components/UI";
import { LangContext, resolveLang, useL, type L } from "./i18n";
import { Advanced } from "./pages/Advanced";
import { Budget } from "./pages/Budget";
import { Diagnostics } from "./pages/Diagnostics";
import { Help } from "./pages/Help";
import { History } from "./pages/History";
import { Home } from "./pages/Home";
import { Hosts } from "./pages/Hosts";
import { Learning } from "./pages/Learning";
import { Models } from "./pages/Models";
import { Onboarding } from "./pages/Onboarding";
import { Privacy } from "./pages/Privacy";
import { useApp } from "./state";

function BootScreen() {
  const app = useApp();
  const l = useL();
  return (
    <div className="boot-screen">
      <div>
        <div className="boot-mark" aria-hidden="true">m</div>
        <h1>morphd</h1>
        <p className="sub mt-8">{l("正在连接本地服务…", "Connecting to the local service…")}</p>
        <div className="skeleton-stack mt-16" aria-hidden="true">
          <div className="skeleton" />
          <div className="skeleton" />
        </div>
        {app.fatal ? (
          <p className="sub mt-16">{l("如果页面一直停在这里，请重新打开宿主后从菜单进入。", "If this persists, reopen the host and launch morphd from its menu.")}</p>
        ) : null}
      </div>
    </div>
  );
}

function Disconnected() {
  const l = useL();
  return (
    <div className="boot-screen">
      <div className="disconnected-panel">
        <div className="boot-mark" aria-hidden="true">m</div>
        <h1>{l("已断开", "Disconnected")}</h1>
        <p className="sub mt-8">
          {l(
            "宿主或 morphd 已退出。页面不会在后台继续工作，也不会继续产生费用。",
            "The host or morphd exited. This page does no work in the background and incurs no cost.",
          )}
        </p>
        <div className="note note-warn mt-16">
          <Icon name="wifi" size={20} />
          <div>
            <strong>{l("如何重新打开", "How to reopen")}</strong>
            <p className="sub">
              {l(
                "重新启动宿主，使用适配时生成的 wrapper，或从宿主菜单打开 morphd。浏览器标签无法自行恢复。",
                "Restart the host with the adapted wrapper, or open morphd from the host menu. A browser tab cannot recover itself.",
              )}
            </p>
          </div>
        </div>
        <div className="row mt-16">
          <Button icon="refresh" onClick={() => window.location.reload()}>
            {l("重新尝试", "Try again")}
          </Button>
          <Button variant="ghost" onClick={() => window.close()}>
            {l("关闭页面", "Close page")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function PageSwitch() {
  const app = useApp();
  switch (app.page) {
    case "hosts":
      return <Hosts />;
    case "models":
      return <Models />;
    case "learning":
      return <Learning />;
    case "budget":
      return <Budget />;
    case "history":
      return <History />;
    case "privacy":
      return <Privacy />;
    case "diagnostics":
      return <Diagnostics />;
    case "help":
      return <Help />;
    case "advanced":
      return <Advanced />;
    default:
      return <Home />;
  }
}

function Shell() {
  const app = useApp();
  const lang = useMemo(
    () => resolveLang(app.settings?.ui.language || "system", navigator.language),
    [app.settings?.ui.language],
  );
  const l: L = useMemo(
    () => (zh: string, en: string) => (lang === "zh" ? zh : en),
    [lang],
  );
  const theme = app.settings?.ui.theme || "system";
  const font = app.settings?.ui.font_size || "medium";
  const contrast = app.settings?.ui.contrast || "normal";
  const motionSetting = app.settings?.ui.reduced_motion || "system";
  const motion = motionSetting === "on" ? "reduce" : motionSetting === "off" ? "normal" : "system";

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = theme;
    root.dataset.font = font;
    root.dataset.contrast = contrast;
    root.dataset.motion = motion;
    root.lang = lang === "zh" ? "zh-CN" : "en";
    document.title = app.page === "home" ? "morphd" : `${app.page} · morphd`;
  }, [theme, font, contrast, motion, lang, app.page]);

  if (app.booting) {
    return (
      <LangContext.Provider value={{ lang, l }}>
        <BootScreen />
      </LangContext.Provider>
    );
  }
  if (!app.connected) {
    return (
      <LangContext.Provider value={{ lang, l }}>
        <Disconnected />
      </LangContext.Provider>
    );
  }
  return (
    <LangContext.Provider value={{ lang, l }}>
      {app.onboarding ? (
        <Onboarding />
      ) : (
        <Layout>
          <PageSwitch />
        </Layout>
      )}
      <ToastHost toasts={app.toasts} onDismiss={app.dismissToast} />
    </LangContext.Provider>
  );
}

class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Do not print session content; the error is local to the renderer.
    console.error("morphd ui error", error.name, info.componentStack?.slice(0, 400));
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="boot-screen">
          <div className="disconnected-panel">
            <div className="boot-mark" aria-hidden="true">m</div>
            <h1>界面遇到问题 / UI error</h1>
            <p className="sub mt-8">
              没有写入任何 harness 文件。刷新页面即可重新连接；如果反复出现，请打开诊断页并复制脱敏诊断信息。
              <br />
              No harness file was written. Reload to reconnect; if it repeats, copy redacted
              diagnostics from the Diagnostics page.
            </p>
            <Button className="mt-16" icon="refresh" onClick={() => window.location.reload()}>
              刷新 / Reload
            </Button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  return (
    <Boundary>
      <Shell />
    </Boundary>
  );
}
