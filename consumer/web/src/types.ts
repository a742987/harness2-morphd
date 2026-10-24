export type Page =
  | "home"
  | "hosts"
  | "models"
  | "learning"
  | "budget"
  | "history"
  | "privacy"
  | "diagnostics"
  | "help"
  | "advanced";

export type Lang = "zh" | "en";
export type Theme = "system" | "light" | "dark";

export interface BudgetNumbers {
  monthly: { limit: number; spent: number; committed: number; remaining: number };
  session: { limit: number; spent: number; committed: number; remaining: number };
  job: { limit: number; spent: number; committed: number; remaining: number };
  alert_ratio: number;
  alert: boolean;
  blocked: boolean;
  reason: string;
  by_kind?: { kind: string; label: string; usd: number }[];
}

export interface Status {
  version: number;
  app_version: string;
  paused: boolean;
  incognito: boolean;
  hosts: {
    host: string;
    label: string;
    version: string;
    integration: string;
    status: string;
    connected: boolean;
    wrapper?: string;
    project_root?: string;
  }[];
  connected_hosts: string[];
  learning: {
    enabled: boolean;
    paused: boolean;
    paused_until: string | null;
    paused_forever: boolean;
    vacation: boolean;
    incognito: boolean;
    incognito_next: boolean;
    incognito_sessions: string[];
    active_sessions?: { session_id: string; host: string; started_at?: string }[];
    focus: boolean;
    trust_level: string;
    change_appetite: string;
  };
  budget: BudgetNumbers;
  job: { running: string | null; queued: number };
  tray: { available: boolean; status: string };
  vault: { keychain: boolean; vault: boolean; vault_unlocked: boolean };
  feedback?: {
    prompts: {
      project_id: string;
      downranked: boolean;
      counts: Record<string, number>;
      quiet_until?: string | null;
    }[];
  };
  started_at: string;
}

export interface Settings {
  version: number;
  enabled: boolean;
  trust_level: "observer" | "advisor" | "assistant" | "autopilot";
  change_appetite: "conservative" | "balanced" | "bold";
  auto_apply: boolean;
  auto_apply_window: "any" | "quiet" | "custom";
  auto_rollback: boolean;
  trial_sessions: number;
  undo_days: number;
  autopilot_ack: boolean;
  budget: {
    monthly_usd: number;
    per_session_usd: number;
    per_job_usd: number;
    alert_ratio: number;
    confirm_before_analysis: boolean;
  };
  cadence: {
    focus_mode: boolean;
    quiet_hours: [string, string] | null;
    weekly_cap: number;
    digest: "off" | "daily" | "weekly" | "only_inbox";
    system_notifications: boolean;
    notification_sound: boolean;
    start_minimized: boolean;
    suggestion_badge: boolean;
    close_window: "tray" | "exit";
    idle_delay_minutes: number;
    tray_enabled: boolean;
    auto_apply_window_start: string;
    auto_apply_window_end: string;
  };
  privacy: {
    telemetry: boolean;
    retention_days: number;
    excluded_globs: string[];
    local_only: boolean;
    auto_discover_hosts: boolean;
    paused_session_recording: "minimal" | "none" | "full";
    snapshot_limit: number;
  };
  ui: {
    theme: Theme;
    language: "system" | Lang;
    font_size: "small" | "medium" | "large" | "xlarge";
    contrast: "normal" | "high";
    reduced_motion: "system" | "on" | "off";
    style: "concise" | "detailed";
    beginner: boolean;
    terminology_tooltips: boolean;
    screen_reader_optimized: boolean;
  };
  models: {
    preset: "frugal" | "balanced" | "quality" | "local" | "custom";
    proposer: string;
    composer: string;
    selector: string;
    judge: string | null;
    fallbacks: string[];
  };
  analysis: { depth: "adaptive" | "light" | "full"; k: number; modes: "parallel" | "sequential" };
  enterprise: { strict: boolean; dual_approval: boolean; container: boolean; egress_allowlist: string[] };
}

export interface ProviderPrice {
  input_per_million: number;
  cached_input_per_million: number;
  cache_write_per_million: number;
  output_per_million: number;
  reasoning_per_million: number;
  per_call_usd: number;
  confirmed: boolean;
}

export interface Provider {
  id: string;
  label: string;
  protocol: "openai-compatible" | "anthropic" | "gemini";
  base_url: string;
  model: string;
  context_window: number;
  max_output: number;
  supports_tools: boolean;
  supports_json: boolean;
  price: ProviderPrice;
  data_policy: string;
  data_policy_ack: boolean;
  help_url: string;
  local?: boolean;
  has_key?: boolean;
  key_hint?: string;
  created_at?: string;
  updated_at?: string;
}

export interface ProviderTestStep {
  name: string;
  ok: boolean;
  detail: string;
}

export interface ProviderTestResult {
  ok: boolean;
  steps: ProviderTestStep[];
  latency_ms: number;
  model: string;
  provider: string;
  estimated_cost_usd: number;
  tool_mode_possible: boolean | null;
  price_known: boolean;
}

export interface ProvidersPayload {
  providers: Provider[];
  catalog: {
    id: string;
    label: string;
    protocol: string;
    base_url: string;
    default_model: string;
    model?: string;
    context_window?: number;
    max_output?: number;
    supports_tools?: boolean;
    supports_json?: boolean;
    price?: ProviderPrice;
    help_url: string;
    data_policy: string;
    local: boolean;
  }[];
  presets: Record<
    string,
    { label: string; analysis_depth: string; provider: string; rationale: string }
  >;
  vault: { keychain: boolean; vault: boolean; vault_unlocked: boolean };
}

export interface HostCandidate {
  id: string;
  kind: "dsh" | "claude-code" | "codex";
  label: string;
  version: string;
  executable: string;
  install_dir: string;
  config_dir: string;
  config_writable?: boolean;
  identity: string;
  compatible: boolean;
  status: string;
  reason: string;
  integration: string;
  installed?: boolean;
  installed_version?: string;
  upgrade_available?: boolean;
  repair_required?: boolean;
  record_id?: string;
  project_root?: string;
  wrapper?: string;
}

export interface ManualProject {
  id: string;
  host: "dsh" | "claude-code" | "codex";
  project_root: string;
  config_dir: string | null;
  label: string;
  read_only: true;
  auto_trigger: false;
  created_at: string;
  updated_at: string;
}

export interface InstallRecord {
  id: string;
  host: string;
  version: string;
  executable: string;
  config_dir: string;
  project_root?: string;
  integration: string;
  wrapper: string;
  status: string;
  backups: { path: string; backup_path: string; sha256: string }[];
  created: string[];
  installed_at: string;
  notes: string;
}

export interface SessionSignals {
  achieved?: boolean | null;
  delivered?: boolean | null;
  corrections?: number;
  tool_loops?: number;
  failures?: string[];
  crash?: boolean;
}

export interface SessionRecord {
  id: string;
  host: string;
  project_id: string;
  project: string;
  started_at: string;
  ended_at: string;
  end_reason: string;
  signals: SessionSignals;
  status: string;
  job_id?: string | null;
  user_turns_count: number;
  summary: string;
  redaction?: { removed_files: number; redacted_fields: string[] };
  error?: { code: string; message: string };
  cost_usd?: number | null;
}

export interface Candidate {
  id: string;
  job_id: string;
  session_id: string;
  host: string;
  project_id: string;
  project: string;
  base_hash: string;
  candidate_hash: string;
  files_changed: string[];
  added_files: string[];
  kind: string;
  diff_path: string;
  summary: string;
  expected_effect: string;
  risk: string;
  cost_usd: number;
  confidence: "high" | "normal";
  safety: { ok: boolean; checks: string[]; violations: string[]; findings: unknown[] };
  fallback_used?: boolean;
  model?: string;
  model_used?: string;
  state: string;
  presented: boolean;
  presentation_reason: string;
  present_after?: string;
  created_at: string;
  applied_at: string | null;
  reverted_at: string | null;
  trial: {
    n: number;
    baseline: Record<string, number | null>;
    observed: { session_id: string; ended_at: string; signals: SessionSignals }[];
    started_at?: string;
  };
  feedback: { reason: string; note: string; at: string }[];
  source: { session_id: string; ended_at: string; signals: SessionSignals };
  automatic?: boolean;
  diff?: string;
}

export interface JobRecord {
  id: string;
  session_id: string;
  host: string;
  project_id: string;
  state: string;
  created_at: string;
  updated_at: string;
  attempts: number;
  candidate_id: string | null;
  cost_usd: number | null;
  reason: string;
  checkpoint: Record<string, unknown>;
  error: { error?: { code: string; message: string; action: string } } | null;
}

export interface ProjectSettings {
  id: string;
  root: string;
  enabled: boolean;
  sensitive: boolean;
  local_only: boolean;
  domain: string;
  excluded_globs: string[];
  retention_days: number;
  budget: Record<string, number>;
  cadence: Record<string, unknown>;
  trust_level: string | null;
  change_appetite: string | null;
}

export interface Diagnostics {
  app_version: string;
  home: string;
  permissions_ok: boolean;
  disk: { bytes: number; files: number };
  hosts: { name: string; ok: boolean; detail: string }[];
  providers: {
    id: string;
    label: string;
    protocol: string;
    model: string;
    key_configured: boolean;
    price_confirmed: boolean;
    local?: boolean;
  }[];
  budget: Settings["budget"];
  logs_tail: { at?: string; event?: string; [key: string]: unknown }[];
  checks: { id?: string; name: string; ok: boolean; detail: string }[];
}

export interface ApiErrorBody {
  error: { code: string; message: string; action: string; details: Record<string, unknown> };
}
