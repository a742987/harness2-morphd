import type {
  ApiErrorBody,
  BudgetNumbers,
  Candidate,
  Diagnostics,
  HostCandidate,
  InstallRecord,
  JobRecord,
  ManualProject,
  ProjectSettings,
  Provider,
  ProviderTestResult,
  ProvidersPayload,
  SessionRecord,
  Settings,
  Status,
} from "./types";

export class ApiError extends Error {
  code: string;
  action: string;
  details: Record<string, unknown>;
  status: number;

  constructor(status: number, body: Partial<ApiErrorBody> | null) {
    const error = body?.error;
    super(error?.message || `HTTP ${status}`);
    this.code = error?.code || "INTERNAL";
    this.action = error?.action || "";
    this.details = error?.details || {};
    this.status = status;
  }
}

let csrfToken = "";
let connected = false;

declare global {
  interface Window {
    morphd?: {
      pickFolder?: (kind: string) => Promise<{ path?: string } | null>;
    };
  }
}

const TOKEN_KEY = "morphd.bootstrap";
const CSRF_KEY = "morphd.csrf";

function bootstrapToken(): string {
  const hash = window.location.hash.replace(/^#/, "");
  const fromHash = new URLSearchParams(hash).get("bootstrap") || "";
  if (fromHash) {
    try {
      window.sessionStorage.setItem(TOKEN_KEY, fromHash);
    } catch {
      /* storage can be unavailable in hardened browsers */
    }
    // Remove the token from the visible address while preserving the page route.
    const rest = hash
      .split("&")
      .filter((part) => !part.startsWith("bootstrap="))
      .join("&");
    const page = window.location.hash.startsWith("#/")
      ? window.location.hash
      : rest
        ? `#${rest}`
        : "#/home";
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}${page}`);
    return fromHash;
  }
  try {
    return window.sessionStorage.getItem(TOKEN_KEY) || "";
  } catch {
    return "";
  }
}

export async function exchange(): Promise<boolean> {
  const token = bootstrapToken();
  if (!token) return false;
  try {
    const response = await fetch("/api/auth/exchange", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bootstrap_token: token }),
      credentials: "same-origin",
    });
    if (!response.ok) return false;
    const payload = (await response.json()) as { csrf_token?: string };
    csrfToken = payload.csrf_token || "";
    try {
      window.sessionStorage.setItem(CSRF_KEY, csrfToken);
    } catch {
      /* storage can be unavailable */
    }
    connected = Boolean(csrfToken);
    return connected;
  } catch {
    return false;
  }
}

export function isConnected(): boolean {
  return connected;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  if (!csrfToken) {
    try {
      csrfToken = window.sessionStorage.getItem(CSRF_KEY) || "";
    } catch {
      csrfToken = "";
    }
  }
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-Morphd-CSRF"] = csrfToken;
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "same-origin",
      cache: "no-store",
    });
  } catch {
    connected = false;
    throw new ApiError(0, {
      error: {
        code: "DISCONNECTED",
        message: "本地服务已断开。",
        action: "重新打开 harness2-morphd 或重启宿主。",
        details: {},
      },
    });
  }
  const text = await response.text();
  let payload: unknown = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = {};
  }
  if (!response.ok) {
    if (response.status === 401) {
      connected = false;
      csrfToken = "";
    }
    throw new ApiError(response.status, payload as Partial<ApiErrorBody>);
  }
  return payload as T;
}

export const api = {
  status: () => request<Status>("GET", "/api/status"),
  settings: () => request<Settings>("GET", "/api/settings"),
  saveSettings: (settings: Partial<Settings>) => request<Settings>("PUT", "/api/settings", settings),
  resetSettings: () => request<Settings>("POST", "/api/settings/reset", { confirm: true }),
  providers: async (): Promise<ProvidersPayload> => {
    const [providers, catalog] = await Promise.all([
      request<Provider[]>("GET", "/api/providers"),
      request<Omit<ProvidersPayload, "providers">>("GET", "/api/providers/catalog"),
    ]);
    return { ...catalog, providers };
  },
  addProvider: (payload: Record<string, unknown>) =>
    request<Provider>("POST", "/api/providers", payload),
  updateProvider: (id: string, payload: Record<string, unknown>) =>
    request<Provider>("PUT", `/api/providers/${encodeURIComponent(id)}`, payload),
  deleteProvider: (id: string) =>
    request<{ ok: boolean }>("DELETE", `/api/providers/${encodeURIComponent(id)}`),
  deleteAllProviders: () =>
    request<{ ok: boolean; deleted: number; ids: string[] }>("DELETE", "/api/providers", {
      confirm: true,
    }),
  testProvider: (id: string | null, payload: Record<string, unknown>) =>
    id
      ? request<ProviderTestResult>("POST", `/api/providers/${encodeURIComponent(id)}/test`, payload)
      : request<ProviderTestResult>("POST", "/api/providers/test", payload),
  vaultUnlock: (passphrase: string) =>
    request<{ vault_unlocked: boolean }>("POST", "/api/vault/unlock", { passphrase }),
  vaultLock: () => request<{ vault_unlocked: boolean }>("POST", "/api/vault/lock", {}),
  hosts: () => request<HostCandidate[]>("GET", "/api/hosts"),
  discoverHosts: () => request<HostCandidate[]>("POST", "/api/hosts/discover", {}),
  adaptHost: (payload: Record<string, unknown>) =>
    request<InstallRecord>("POST", "/api/hosts/adapt", { ...payload, confirm: true }),
  uninstallHost: (host: string) =>
    request<{ ok: boolean }>("POST", "/api/hosts/uninstall", { host, confirm: true }),
  pickFolder: async (kind: string) => {
    const bridge = typeof window !== "undefined" ? window.morphd?.pickFolder : undefined;
    if (bridge) {
      const result = await bridge(kind);
      return { path: String(result?.path || "") };
    }
    return request<{ path: string }>("POST", "/api/hosts/pick-folder", { kind });
  },
  manualProjects: () =>
    request<{ projects: ManualProject[] }>("GET", "/api/manual-projects"),
  addManualProject: (payload: Record<string, unknown>) =>
    request<ManualProject>("POST", "/api/manual-projects", payload),
  deleteManualProject: (id: string) =>
    request<{ ok: boolean }>("DELETE", `/api/manual-projects/${encodeURIComponent(id)}`),
  importManualProject: (id: string) =>
    request<{ session: SessionRecord; source: string }>(
      "POST",
      `/api/manual-projects/${encodeURIComponent(id)}/import`,
      {},
    ),
  restoreOriginal: (host: string, projectRoot?: string) =>
    request<{ ok: boolean; snapshot_id: string }>("POST", "/api/hosts/restore-original", {
      host,
      project_root: projectRoot,
      confirm: true,
    }),
  sessions: (limit = 100, project?: string) =>
    request<SessionRecord[]>(
      "GET",
      `/api/sessions?limit=${limit}${project ? `&project=${encodeURIComponent(project)}` : ""}`,
    ),
  analyzeSession: (id: string, confirm: boolean) =>
    request<{
      requires_confirmation?: boolean;
      preview?: Record<string, unknown>;
      job_id?: string;
      ok?: boolean;
    }>("POST", `/api/sessions/${encodeURIComponent(id)}/analyze`, { confirm }),
  incognito: (active: boolean, sessionId?: string) =>
    request<Record<string, unknown>>("POST", "/api/sessions/incognito", {
      active,
      session_id: sessionId,
    }),
  jobs: (state?: string) =>
    request<JobRecord[]>(
      "GET",
      `/api/jobs${state ? `?state=${encodeURIComponent(state)}` : ""}`,
    ),
  cancelJob: (id: string) =>
    request<JobRecord>("POST", `/api/jobs/${encodeURIComponent(id)}/cancel`, {}),
  candidates: (state?: string, project?: string) =>
    request<Candidate[]>(
      "GET",
      `/api/candidates${state ? `?state=${encodeURIComponent(state)}` : ""}${
        project ? `${state ? "&" : "?"}project=${encodeURIComponent(project)}` : ""
      }`,
    ),
  candidate: (id: string) =>
    request<Candidate>("GET", `/api/candidates/${encodeURIComponent(id)}`),
  applyCandidate: (id: string) =>
    request<Candidate>("POST", `/api/candidates/${encodeURIComponent(id)}/apply`, { confirm: true }),
  revertCandidate: (id: string) =>
    request<Candidate>("POST", `/api/candidates/${encodeURIComponent(id)}/revert`, { confirm: true }),
  feedback: (id: string, reason: string, note = "") =>
    request<{ ok: boolean }>("POST", `/api/candidates/${encodeURIComponent(id)}/feedback`, {
      reason,
      note,
    }),
  respondFeedback: (projectId: string, reason: string) =>
    request<{ ok: boolean }>("POST", "/api/feedback/respond", {
      project_id: projectId,
      reason,
    }),
  budget: (project?: string) =>
    request<BudgetNumbers>(
      "GET",
      `/api/budget${project ? `?project=${encodeURIComponent(project)}` : ""}`,
    ),
  pause: (until: string | null) =>
    request<Record<string, unknown>>("POST", "/api/learning/pause", { until }),
  resume: () => request<Record<string, unknown>>("POST", "/api/learning/resume", {}),
  vacation: (active: boolean) =>
    request<Record<string, unknown>>("POST", "/api/learning/vacation", { active }),
  preview: (sessionId: string) =>
    request<Record<string, unknown>>(
      "GET",
      `/api/privacy/preview?session_id=${encodeURIComponent(sessionId)}`,
    ),
  deleteData: (payload: Record<string, unknown>) =>
    request<Record<string, unknown>>("POST", "/api/privacy/delete", { ...payload, confirm: true }),
  exportData: () => request<{ path: string; bundle: Record<string, unknown> }>("GET", "/api/privacy/export"),
  retention: () => request<Record<string, number>>("POST", "/api/privacy/retention", {}),
  diagnostics: () => request<Diagnostics>("GET", "/api/diagnostics"),
  projects: () => request<ProjectSettings[]>("GET", "/api/projects"),
  project: (id: string) => request<ProjectSettings>("GET", `/api/projects/${encodeURIComponent(id)}`),
  saveProject: (id: string, payload: Partial<ProjectSettings>) =>
    request<ProjectSettings>("PUT", `/api/projects/${encodeURIComponent(id)}`, payload),
  resetLearning: () =>
    request<Record<string, unknown>>("POST", "/api/diagnostics/reset-learning", { confirm: true }),
  completeOnboarding: () =>
    request<{ ok: boolean }>("POST", "/api/onboarding/complete", {}),
};
