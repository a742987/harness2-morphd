import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, ApiError, exchange } from "./api";
import type {
  BudgetNumbers,
  Candidate,
  Diagnostics,
  HostCandidate,
  JobRecord,
  Page,
  ProjectSettings,
  ProvidersPayload,
  SessionRecord,
  Settings,
  Status,
} from "./types";

export interface Toast {
  id: number;
  kind: "ok" | "warn" | "info";
  zh: string;
  en: string;
}

interface AppValue {
  booting: boolean;
  connected: boolean;
  fatal: string;
  page: Page;
  setPage: (page: Page) => void;
  status: Status | null;
  settings: Settings | null;
  providers: ProvidersPayload | null;
  hosts: HostCandidate[];
  sessions: SessionRecord[];
  candidates: Candidate[];
  jobs: JobRecord[];
  budget: BudgetNumbers | null;
  diagnostics: Diagnostics | null;
  projects: ProjectSettings[];
  toasts: Toast[];
  onboarding: boolean;
  dismissOnboarding: () => void;
  refreshAll: (quiet?: boolean) => Promise<void>;
  refreshData: () => Promise<void>;
  saveSettings: (patch: Partial<Settings>) => Promise<Settings | null>;
  resetSettings: () => Promise<void>;
  toast: (kind: Toast["kind"], zh: string, en: string) => void;
  dismissToast: (id: number) => void;
  handleError: (error: unknown) => ApiError | null;
}

const AppContext = createContext<AppValue | null>(null);

export function useApp(): AppValue {
  const value = useContext(AppContext);
  if (!value) throw new Error("useApp outside provider");
  return value;
}

function mergeSettings(current: Settings, patch: Partial<Settings>): Settings {
  const next: Record<string, unknown> = { ...current };
  for (const [key, value] of Object.entries(patch)) {
    if (value && typeof value === "object" && !Array.isArray(value) && current[key as keyof Settings]) {
      next[key] = { ...(current[key as keyof Settings] as object), ...(value as object) };
    } else {
      next[key] = value;
    }
  }
  return next as unknown as Settings;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [booting, setBooting] = useState(true);
  const [connected, setConnected] = useState(false);
  const [fatal, setFatal] = useState("");
  const initialPage = (): Page => {
    const raw = window.location.hash.replace(/^#\/?/, "");
    const allowed: Page[] = [
      "home",
      "hosts",
      "models",
      "learning",
      "budget",
      "history",
      "privacy",
      "diagnostics",
      "help",
      "advanced",
    ];
    return (allowed.includes(raw as Page) ? raw : "home") as Page;
  };
  const [page, setPageState] = useState<Page>(initialPage);
  const [status, setStatus] = useState<Status | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [providers, setProviders] = useState<ProvidersPayload | null>(null);
  const [hosts, setHosts] = useState<HostCandidate[]>([]);
  const [sessions, setSessions] = useState<SessionRecord[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [budget, setBudget] = useState<BudgetNumbers | null>(null);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [projects, setProjects] = useState<ProjectSettings[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [onboarding, setOnboarding] = useState(false);
  const toastId = useRef(0);
  const dismissed = useRef(false);

  const toast = useCallback((kind: Toast["kind"], zh: string, en: string) => {
    const id = ++toastId.current;
    setToasts((current) => [...current, { id, kind, zh, en }]);
    window.setTimeout(() => {
      setToasts((current) => current.filter((item) => item.id !== id));
    }, 5200);
  }, []);

  const dismissToast = useCallback((id: number) => {
    setToasts((current) => current.filter((item) => item.id !== id));
  }, []);

  const handleError = useCallback(
    (error: unknown): ApiError | null => {
      if (error instanceof ApiError) {
        if (error.code === "AUTH_REQUIRED" || error.code === "DISCONNECTED") {
          setConnected(false);
        }
        return error;
      }
      return new ApiError(0, {
        error: {
          code: "INTERNAL",
          message: error instanceof Error ? error.message : String(error),
          action: "",
          details: {},
        },
      });
    },
    [],
  );

  const refreshStatus = useCallback(async () => {
    try {
      const next = await api.status();
      setStatus(next);
      setConnected(true);
      return next;
    } catch (error) {
      handleError(error);
      return null;
    }
  }, [handleError]);

  const refreshData = useCallback(async () => {
    try {
      const [
        nextStatus,
        nextSettings,
        nextProviders,
        nextHosts,
        nextSessions,
        nextCandidates,
        nextJobs,
        nextBudget,
        nextDiagnostics,
        nextProjects,
      ] = await Promise.all([
        api.status(),
        api.settings(),
        api.providers(),
        api.hosts(),
        api.sessions(100),
        api.candidates(),
        api.jobs(),
        api.budget(),
        api.diagnostics(),
        api.projects(),
      ]);
      setStatus(nextStatus);
      setSettings(nextSettings);
      setProviders(nextProviders);
      setHosts(nextHosts.hosts);
      setSessions(nextSessions.sessions);
      setCandidates(nextCandidates.candidates);
      setJobs(nextJobs.jobs);
      setBudget(nextBudget);
      setDiagnostics(nextDiagnostics);
      setProjects(nextProjects.projects);
      setConnected(true);
      setFatal("");
      if (!dismissed.current) {
        const missing =
          nextHosts.hosts.filter((h) => h.installed || h.compatible).length === 0 ||
          nextProviders.providers.length === 0 ||
          !nextSettings.models.proposer;
        setOnboarding(missing);
      }
    } catch (error) {
      const apiError = handleError(error);
      if (apiError && (apiError.code === "AUTH_REQUIRED" || apiError.code === "DISCONNECTED")) {
        setConnected(false);
      } else if (apiError) {
        toast("warn", apiError.message, apiError.message);
      }
    }
  }, [handleError, toast]);

  const refreshAll = useCallback(
    async (quiet = false) => {
      try {
        const nextStatus = await refreshStatus();
        if (!nextStatus && !quiet) setConnected(false);
        if (nextStatus) await refreshData();
      } catch {
        /* handled */
      }
    },
    [refreshData, refreshStatus],
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const ok = await exchange();
      if (cancelled) return;
      if (!ok) {
        // The cookie may still be valid from a previous exchange in this server lifetime.
        try {
          const next = await api.status();
          setStatus(next);
          setConnected(true);
          await refreshData();
        } catch {
          setConnected(false);
          setFatal("disconnected");
        }
      } else {
        setConnected(true);
        await refreshData();
      }
      if (!cancelled) setBooting(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshData]);

  useEffect(() => {
    if (!connected) return;
    let tick = 0;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      tick += 1;
      if (tick % 4 === 0) void refreshAll(true);
      else void refreshStatus();
    }, 8000);
    const onVisible = () => {
      if (document.visibilityState === "visible") void refreshAll(true);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [connected, refreshAll, refreshStatus]);

  const saveSettings = useCallback(
    async (patch: Partial<Settings>) => {
      try {
        const saved = await api.saveSettings(patch);
        setSettings(saved);
        setStatus((current) => (current ? { ...current, learning: { ...current.learning, enabled: saved.enabled } } : current));
        return saved;
      } catch (error) {
        const apiError = handleError(error);
        if (apiError && apiError.code !== "AUTH_REQUIRED" && apiError.code !== "DISCONNECTED") {
          toast("warn", `没有保存：${apiError.message}`, `Not saved: ${apiError.message}`);
        }
        return null;
      }
    },
    [handleError, toast],
  );

  const resetSettings = useCallback(async () => {
    try {
      const saved = await api.resetSettings();
      setSettings(saved);
      setStatus((current) => (current ? { ...current, learning: { ...current.learning, enabled: saved.enabled } } : current));
      toast("ok", "设置已重置为默认值", "Settings reset to defaults");
    } catch (error) {
      const apiError = handleError(error);
      if (apiError) toast("warn", apiError.message, apiError.message);
    }
  }, [handleError, toast]);

  const setPage = useCallback((next: Page) => {
    setPageState(next);
    window.history.replaceState(null, "", `#/${next}`);
    window.scrollTo({ top: 0, behavior: "auto" });
  }, []);

  useEffect(() => {
    const onHash = () => {
      const raw = window.location.hash.replace(/^#\/?/, "");
      if (raw) setPageState(raw as Page);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const dismissOnboarding = useCallback(() => {
    dismissed.current = true;
    setOnboarding(false);
  }, []);

  const value = useMemo<AppValue>(
    () => ({
      booting,
      connected,
      fatal,
      page,
      setPage,
      status,
      settings,
      providers,
      hosts,
      sessions,
      candidates,
      jobs,
      budget,
      diagnostics,
      projects,
      toasts,
      onboarding,
      dismissOnboarding,
      refreshAll,
      refreshData,
      saveSettings,
      resetSettings,
      toast,
      dismissToast,
      handleError,
    }),
    [
      booting,
      connected,
      fatal,
      page,
      setPage,
      status,
      settings,
      providers,
      hosts,
      sessions,
      candidates,
      jobs,
      budget,
      diagnostics,
      projects,
      toasts,
      onboarding,
      dismissOnboarding,
      refreshAll,
      refreshData,
      saveSettings,
      resetSettings,
      toast,
      dismissToast,
      handleError,
    ],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useSettingsPatch(): (patch: Partial<Settings>) => Promise<Settings | null> {
  const { settings, saveSettings } = useApp();
  return useCallback(
    (patch: Partial<Settings>) => {
      if (!settings) return Promise.resolve(null);
      return saveSettings(patch);
    },
    [settings, saveSettings],
  );
}

export function withDeepMerge(settings: Settings, patch: Partial<Settings>): Settings {
  return mergeSettings(settings, patch);
}
