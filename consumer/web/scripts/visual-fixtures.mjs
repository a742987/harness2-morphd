#!/usr/bin/env node
// Reproducible seed data and local-service lifecycle for the visual/a11y matrix.
//
// The records written here follow the same versioned JSON layouts used by
// morphd/consumer/store.py, service.py and tests/test_consumer.py. They are fixtures for
// screenshots only: the temporary MORPHD_HOME is removed when the run finishes.
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  closeSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const repoRoot = fileURLToPath(new URL("../../..", import.meta.url));
export const isWindows = process.platform === "win32";
const repoTenV = join(repoRoot, ".venv-win", "Scripts", "python.exe");
export const pythonExe =
  process.env.MORPHD_VISUAL_PYTHON?.trim() ||
  (existsSync(repoTenV) ? repoTenV : "python");

export function sha256(text) {
  return createHash("sha256").update(String(text), "utf8").digest("hex");
}

function isoDaysAgo(days, hour = 10) {
  const date = new Date(Date.now() - days * 86400000);
  date.setUTCHours(hour, 15, 0, 0);
  return date.toISOString();
}

function writeJson(file, payload) {
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}

function writeText(file, text) {
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, text, "utf8");
}

function appendJsonl(file, rows) {
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, `${rows.map((row) => JSON.stringify(row)).join("\n")}\n`, "utf8");
}

export function makeRunRoot() {
  if (!isWindows) {
    throw new Error(
      "The visual matrix needs Windows Node so it can launch Microsoft Edge and .venv-win\\Scripts\\python.exe. " +
        "Run it from PowerShell/CMD on Windows (WSL can edit the repo, not host the browser).",
    );
  }
  const root = mkdtempSync(join(tmpdir(), "morphd-visual-"));
  const home = join(root, "home");
  const blankHome = join(root, "blank-home");
  mkdirSync(home, { recursive: true });
  mkdirSync(blankHome, { recursive: true });
  return { root, home, blankHome };
}

function isolatedHomeDirs(root) {
  return {
    user: join(root, "user-profile"),
    appData: join(root, "appdata", "Roaming"),
    localAppData: join(root, "appdata", "Local"),
    programFiles: join(root, "program-files"),
    programFilesX86: join(root, "program-files-x86"),
    dshConfig: join(root, "dsh-home"),
  };
}

function serverEnv(root, home) {
  const dirs = isolatedHomeDirs(root);
  for (const directory of Object.values(dirs)) mkdirSync(directory, { recursive: true });
  const systemRoot = process.env.SystemRoot || "C:\\Windows";
  const pathParts = existsSync(pythonExe)
    ? [dirname(pythonExe), join(systemRoot, "System32"), systemRoot]
    : [...String(process.env.PATH || "").split(";").filter(Boolean), join(systemRoot, "System32"), systemRoot];
  return {
    ...process.env,
    MORPHD_HOME: home,
    MORPHD_TRAY: "0",
    PYTHONUTF8: "1",
    PYTHONIOENCODING: "utf-8",
    NO_COLOR: "1",
    USERPROFILE: dirs.user,
    HOME: dirs.user,
    APPDATA: dirs.appData,
    LOCALAPPDATA: dirs.localAppData,
    ProgramFiles: dirs.programFiles,
    "ProgramFiles(x86)": dirs.programFilesX86,
    // Keep the server able to start; deliberately omit npm/global host locations so a
    // developer machine with dsh/Claude/Codex installed still shows onboarding.
    PATH: pathParts.join(";"),
  };
}

export function seedVisualHome(home) {
  const root = dirname(home);
  const dirs = isolatedHomeDirs(root);
  const project = join(root, "visual-project");
  const workspace = join(home, "sessions", "sess-visual-1", "workspace");
  mkdirSync(project, { recursive: true });
  mkdirSync(workspace, { recursive: true });

  const originalAgents =
    "# Project guidance\n\n- Keep changes small and reviewable.\n- Run the project checks before finishing.\n";
  const appliedAgents =
    `${originalAgents}\n- Before reporting a task complete, run \`npm test\` and include the result.\n`;
  writeText(join(project, "AGENTS.md"), appliedAgents);
  writeText(join(project, "README.md"), "# Visual fixture project\n\nSeeded for UI evidence only.\n");
  writeText(join(workspace, "main.py"), "print('visual fixture')\n");
  writeText(
    join(project, "scripts", "verify.ps1"),
    "Write-Output 'visual fixture check'\n",
  );

  // ProjectManager.project_id: sha256(resolved root)[:12]
  const projectId = `proj_${sha256(resolve(project)).slice(0, 12)}`;
  const providerId = "p_visual";
  const modelRef = `${providerId}/gpt-5-mini`;
  const created = isoDaysAgo(8);

  const settings = {
    version: 1,
    enabled: true,
    trust_level: "advisor",
    change_appetite: "conservative",
    auto_apply: false,
    auto_apply_window: "any",
    auto_rollback: true,
    trial_sessions: 3,
    undo_days: 7,
    autopilot_ack: false,
    budget: {
      monthly_usd: 24,
      per_session_usd: 2,
      per_job_usd: 3,
      alert_ratio: 0.8,
      confirm_before_analysis: false,
    },
    cadence: {
      focus_mode: true,
      quiet_hours: null,
      weekly_cap: 2,
      digest: "weekly",
      system_notifications: false,
      notification_sound: false,
      start_minimized: false,
      suggestion_badge: false,
      close_window: "tray",
      idle_delay_minutes: 0,
      tray_enabled: true,
    },
    privacy: {
      telemetry: false,
      retention_days: 30,
      excluded_globs: ["node_modules", ".env"],
      local_only: false,
      auto_discover_hosts: true,
    },
    ui: {
      theme: "system",
      language: "zh",
      font_size: "medium",
      contrast: "normal",
      reduced_motion: "off",
      style: "concise",
      beginner: true,
      terminology_tooltips: true,
    },
    models: {
      preset: "balanced",
      proposer: modelRef,
      composer: modelRef,
      selector: modelRef,
      judge: null,
      fallbacks: [],
    },
    analysis: { depth: "adaptive", k: 1, modes: "parallel" },
    enterprise: {
      strict: false,
      dual_approval: false,
      container: false,
      egress_allowlist: [],
    },
  };
  writeJson(join(home, "config.json"), settings);

  const price = {
    input_per_million: 1.1,
    cached_input_per_million: 0.275,
    cache_write_per_million: 1.1,
    output_per_million: 4.4,
    reasoning_per_million: 0,
    per_call_usd: 0,
    confirmed: true,
  };
  writeJson(join(home, "providers.json"), {
    version: 1,
    providers: [
      {
        id: providerId,
        label: "OpenAI",
        protocol: "openai-compatible",
        base_url: "https://api.openai.com/v1",
        model: "gpt-5-mini",
        context_window: 128000,
        max_output: 4096,
        supports_tools: true,
        supports_json: true,
        price,
        data_policy: "请求仅发送脱敏后的说明、用户消息和轨迹摘要；不发送凭据或代码全文。",
        data_policy_ack: true,
        help_url: "https://platform.openai.com/api-keys",
        local: false,
        key_ref: "vault:visual-fixture",
        key_hint: "1234",
        created_at: created,
        updated_at: created,
      },
    ],
  });

  const fakeExecutable = join(home, "hosts", "dsh", "bin", "dsh.cmd");
  writeText(fakeExecutable, "@echo off\r\nexit /b 0\r\n");
  mkdirSync(join(dirs.dshConfig, "profiles", "default"), { recursive: true });
  writeText(join(dirs.dshConfig, "profiles", "default", "package.json"), "{}\n");
  const hostRecord = {
    id: "host_dsh_visual",
    host: "dsh",
    version: "0.2.1",
    executable: fakeExecutable,
    install_dir: dirname(fakeExecutable),
    config_dir: dirs.dshConfig,
    project_root: project,
    adapter_version: "0.5.0",
    integration: "wrapper",
    wrapper: fakeExecutable,
    identity: "deepseek",
    status: "adapted",
    backups: [],
    created: [fakeExecutable],
    installed_at: isoDaysAgo(9, 9),
    notes: "Visual evidence fixture",
  };
  writeJson(join(home, "install.json"), {
    version: 1,
    app_version: "0.5.0",
    platform: "win32-x64",
    hosts: [hostRecord],
  });

  writeJson(join(home, "manual-projects.json"), {
    version: 1,
    projects: [
      {
        id: "manual_visual_demo",
        host: "dsh",
        project_root: project,
        config_dir: dirs.dshConfig,
        label: "只读演示项目",
        read_only: true,
        auto_trigger: false,
        created_at: created,
        updated_at: created,
      },
    ],
  });

  writeJson(join(home, "projects", `${projectId}.json`), {
    version: 1,
    id: projectId,
    root: project,
    enabled: true,
    sensitive: false,
    local_only: false,
    domain: "web-app",
    excluded_globs: ["node_modules"],
    retention_days: 30,
    budget: { monthly_usd: 12 },
    cadence: { weekly_cap: 1 },
    trust_level: null,
    change_appetite: null,
    created_at: created,
    updated_at: created,
  });

  const turnTexts = [
    "修复构建失败，并确认测试通过。",
    "不要用 npm，项目使用 pnpm。",
    "请把校验步骤写进项目说明，避免下次重复确认。",
  ];
  const sessions = [
    {
      id: "sess-visual-1",
      host: "dsh",
      project_id: projectId,
      project,
      workspace,
      source_workspace: project,
      events_path: "",
      started_at: isoDaysAgo(1, 9),
      ended_at: isoDaysAgo(1, 10),
      end_reason: "completed",
      user_turns: turnTexts.map((text, index) => ({ text, corrective: index > 0 })),
      signals: { achieved: true, delivered: true, corrections: 2, tool_loops: 1, failures: [] },
      status: "analyzed",
      job_id: "job_visual_1",
      trajectory_summary: "[USER] 修复构建失败\n[ASSISTANT] 已定位 lockfile 不一致\n[USER] 不要用 npm\n[ASSISTANT] 改用 pnpm 并补充检查",
      redaction: { removed_files: 2, redacted_fields: ["api_key", "authorization"] },
      cost_usd: 0.42,
      created_at: isoDaysAgo(1, 10),
    },
    {
      id: "sess-visual-2",
      host: "dsh",
      project_id: projectId,
      project,
      workspace,
      source_workspace: project,
      events_path: "",
      started_at: isoDaysAgo(3, 14),
      ended_at: isoDaysAgo(3, 15),
      end_reason: "completed",
      user_turns: [{ text: "为 API 增加超时处理。", corrective: false }],
      signals: { achieved: true, delivered: true, corrections: 0, tool_loops: 0, failures: [] },
      status: "analyzed",
      job_id: "job_visual_2",
      trajectory_summary: "[USER] 增加超时处理\n[ASSISTANT] 已添加 30s 超时和错误提示",
      cost_usd: 0.33,
      created_at: isoDaysAgo(3, 15),
    },
    {
      id: "sess-visual-3",
      host: "dsh",
      project_id: projectId,
      project,
      workspace,
      source_workspace: project,
      events_path: "",
      started_at: isoDaysAgo(5, 11),
      ended_at: isoDaysAgo(5, 12),
      end_reason: "completed",
      user_turns: [
        { text: "更新首页文案。", corrective: false },
        { text: "不是这个页面，是设置页。", corrective: true },
        { text: "不要把按钮改成灰的。", corrective: true },
      ],
      signals: { achieved: false, delivered: false, corrections: 3, tool_loops: 2, failures: [] },
      status: "analyzed",
      job_id: "job_visual_3",
      trajectory_summary: "[USER] 更新首页文案\n[USER] 不是这个页面\n[ASSISTANT] 已修正到设置页",
      cost_usd: 0.51,
      created_at: isoDaysAgo(5, 12),
    },
  ];
  for (const session of sessions) writeJson(join(home, "sessions", `${session.id}.json`), { version: 1, ...session });

  const diffText = [
    "diff --git a/AGENTS.md b/AGENTS.md",
    "--- a/AGENTS.md",
    "+++ b/AGENTS.md",
    "@@ -1,4 +1,6 @@",
    " # Project guidance",
    " ",
    " - Keep changes small and reviewable.",
    " - Run the project checks before finishing.",
    "+",
    "+- Before reporting a task complete, run `npm test` and include the result.",
    "",
  ].join("\n");
  const needsApplyId = "cand_visual_needs";
  const appliedId = "cand_visual_applied";
  const needsDiff = join(home, "diffs", `${needsApplyId}.diff`);
  const appliedDiff = join(home, "diffs", `${appliedId}.diff`);
  writeText(needsDiff, diffText);
  writeText(appliedDiff, diffText);

  const sourceSignals = { achieved: true, delivered: true, corrections: 2, tool_loops: 1 };
  const needsApply = {
    version: 1,
    id: needsApplyId,
    job_id: "job_visual_1",
    session_id: "sess-visual-1",
    host: "dsh",
    project_id: projectId,
    project,
    base_hash: sha256("visual-base-hash"),
    candidate_hash: sha256("visual-candidate-hash"),
    files_changed: ["AGENTS.md"],
    added_files: [],
    kind: "instruction",
    diff_path: needsDiff,
    summary: "把“完成前运行 npm test”写入项目说明",
    expected_effect: "下次同类任务会先运行测试并给出结果，减少重复纠正。",
    risk: "只改 AGENTS.md 一行，不触碰权限、hooks 或宿主设置；可随时撤销。",
    cost_usd: 0.08,
    confidence: "high",
    safety: {
      ok: true,
      checks: ["surface", "paths", "permissions", "secrets"],
      violations: [],
      findings: [],
    },
    state: "needs_apply",
    presented: true,
    presentation_reason: "high_confidence",
    created_at: isoDaysAgo(1, 10),
    applied_at: null,
    reverted_at: null,
    trial: { n: 0, baseline: {}, observed: [] },
    feedback: [],
    source: {
      session_id: "sess-visual-1",
      ended_at: isoDaysAgo(1, 10),
      signals: sourceSignals,
    },
  };
  const applied = {
    version: 1,
    id: appliedId,
    job_id: "job_visual_2",
    session_id: "sess-visual-2",
    host: "dsh",
    project_id: projectId,
    project,
    base_hash: sha256("visual-base-hash-applied"),
    candidate_hash: sha256(appliedAgents),
    files_changed: ["AGENTS.md"],
    added_files: [],
    kind: "instruction",
    diff_path: appliedDiff,
    summary: "在 AGENTS.md 中补充 pnpm 测试检查",
    expected_effect: "把验证命令固定在项目说明里，下一次会话直接复用。",
    risk: "仅调整说明文件；已有快照，撤销会恢复到应用前内容。",
    cost_usd: 0.11,
    confidence: "normal",
    safety: {
      ok: true,
      checks: ["surface", "paths", "permissions", "secrets"],
      violations: [],
      findings: [],
    },
    state: "trial",
    presented: true,
    presentation_reason: "immediate",
    created_at: isoDaysAgo(2, 16),
    applied_at: isoDaysAgo(2, 16),
    reverted_at: null,
    snapshot_id: "snap_visual_apply",
    applied_hash: sha256(appliedAgents),
    applied_files: { "AGENTS.md": sha256(appliedAgents) },
    trial: {
      n: 2,
      baseline: { n: 2, achieved_rate: 0.5, corrections: 2, tool_loops: 1, turns: 3, session_ids: ["sess-visual-2", "sess-visual-3"] },
      observed: [
        { session_id: "sess-visual-2", ended_at: isoDaysAgo(3, 15), signals: { achieved: true, corrections: 0, tool_loops: 0 } },
        { session_id: "sess-visual-1", ended_at: isoDaysAgo(1, 10), signals: { achieved: true, corrections: 1, tool_loops: 0 } },
      ],
      started_at: isoDaysAgo(2, 16),
    },
    feedback: [],
    source: {
      session_id: "sess-visual-2",
      ended_at: isoDaysAgo(3, 15),
      signals: { achieved: true, delivered: true, corrections: 0, tool_loops: 0 },
    },
    base_tree: { "AGENTS.md": originalAgents },
    candidate_tree: { "AGENTS.md": appliedAgents },
  };
  // applied is written second so History's newest-first order opens it first.
  writeJson(join(home, "candidates", `${needsApplyId}.json`), needsApply);
  writeJson(join(home, "candidates", `${appliedId}.json`), applied);

  const backup = join(home, "snapshots", "snap_visual_apply", "files", "AGENTS.md");
  writeText(backup, originalAgents);
  writeJson(join(home, "snapshots", "snap_visual_apply.json"), {
    version: 1,
    id: "snap_visual_apply",
    host: "dsh",
    project_id: projectId,
    project,
    created_at: isoDaysAgo(2, 16),
    reason: "apply",
    candidate_id: appliedId,
    entries: [
      {
        path: join(project, "AGENTS.md"),
        rel: "AGENTS.md",
        kind: "file",
        backup_path: backup,
        sha256: sha256(originalAgents),
      },
    ],
    base_hash: sha256(`base:${originalAgents}`),
    hashes: { "AGENTS.md": sha256(originalAgents) },
  });

  writeJson(join(home, "jobs", "job_visual_1.json"), {
    version: 1,
    id: "job_visual_1",
    session_id: "sess-visual-1",
    host: "dsh",
    project_id: projectId,
    state: "needs_apply",
    created_at: isoDaysAgo(1, 10),
    updated_at: isoDaysAgo(1, 10),
    attempts: 1,
    candidate_id: needsApplyId,
    cost_usd: 0.08,
    reason: "发现可复用的验证步骤",
    checkpoint: { stage: "candidate" },
    error: null,
    dir: join(home, "jobs", "job_visual_1"),
  });
  writeJson(join(home, "jobs", "job_visual_2.json"), {
    version: 1,
    id: "job_visual_2",
    session_id: "sess-visual-2",
    host: "dsh",
    project_id: projectId,
    state: "applied",
    created_at: isoDaysAgo(2, 16),
    updated_at: isoDaysAgo(2, 16),
    attempts: 1,
    candidate_id: appliedId,
    cost_usd: 0.11,
    reason: "项目说明缺少测试命令",
    checkpoint: { stage: "applied" },
    error: null,
    dir: join(home, "jobs", "job_visual_2"),
  });
  writeJson(join(home, "jobs", "job_visual_3.json"), {
    version: 1,
    id: "job_visual_3",
    session_id: "sess-visual-3",
    host: "dsh",
    project_id: projectId,
    state: "noop",
    created_at: isoDaysAgo(5, 12),
    updated_at: isoDaysAgo(5, 12),
    attempts: 1,
    candidate_id: null,
    cost_usd: 0,
    reason: "证据不足，零成本跳过",
    checkpoint: { stage: "triage" },
    error: null,
    dir: join(home, "jobs", "job_visual_3"),
  });

  const month = new Date().toISOString().slice(0, 7);
  const journal = [
    { at: `${month}-03T09:10:00.000Z`, ts: Date.now() / 1000 - 86400 * 6, event: "charge", kind: "proposal", label: modelRef, usd: 1.8, estimated: true, session_id: "sess-visual-3", job_id: "job_visual_3", project_id: projectId, usage: {} },
    { at: `${month}-08T15:20:00.000Z`, ts: Date.now() / 1000 - 86400 * 3, event: "charge", kind: "proposal", label: modelRef, usd: 0.75, estimated: true, session_id: "sess-visual-2", job_id: "job_visual_2", project_id: projectId, usage: {} },
    { at: `${month}-12T10:05:00.000Z`, ts: Date.now() / 1000 - 86400, event: "charge", kind: "compose", label: "gpt-5-mini", usd: 0.51, estimated: true, session_id: "sess-visual-1", job_id: "job_visual_1", project_id: projectId, usage: {} },
    { at: `${month}-12T10:06:00.000Z`, ts: Date.now() / 1000 - 86400, event: "charge", kind: "select", label: "gpt-5-mini", usd: 0.36, estimated: true, session_id: "sess-visual-1", job_id: "job_visual_1", project_id: projectId, usage: {} },
  ];
  appendJsonl(join(home, "_budget", "consumer.jsonl"), journal);

  writeJson(join(home, "state.json"), {
    version: 1,
    paused_until: null,
    paused_forever: false,
    vacation: false,
    incognito_next: false,
    incognito_sessions: [],
    incognito_done: [],
    active_sessions: [],
    weekly: {},
    feedback: {},
    alerts: {},
    last_digest: null,
    tray_processed: 0,
  });

  appendJsonl(join(home, "logs", "events.jsonl"), [
    { at: isoDaysAgo(9, 9), event: "host_adapted", host: "dsh", version: "0.2.1" },
    { at: isoDaysAgo(8, 9), event: "provider_added", provider_id: providerId, protocol: "openai-compatible" },
    { at: isoDaysAgo(2, 16), event: "candidate_applied", candidate_id: appliedId, host: "dsh", files: ["AGENTS.md"] },
    { at: isoDaysAgo(1, 10), event: "candidate_created", candidate_id: needsApplyId, job_id: "job_visual_1" },
    { at: isoDaysAgo(1, 10), event: "job_completed", job_id: "job_visual_1", state: "needs_apply" },
  ]);

  return {
    project,
    workspace,
    projectId,
    providerId,
    modelRef,
    needsApplyId,
    appliedId,
    originalAgents,
    appliedAgents,
    hostRecord,
  };
}

export function resolveEdge(explicit = "") {
  const candidates = [
    explicit,
    process.env.EDGE,
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
    "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate;
  }
  throw new Error(
    "Microsoft Edge was not found. Set EDGE to msedge.exe. Tried:\n" +
      candidates.map((candidate) => `  - ${candidate}`).join("\n"),
  );
}

async function waitForServerInfo(home, child, logPath, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let exited = false;
  let exitCode = null;
  child.once("exit", (code) => {
    exited = true;
    exitCode = code;
  });
  while (Date.now() < deadline) {
    if (exited) {
      throw new Error(
        `morphd serve exited before writing runtime/server.json (code ${exitCode}). Log: ${logPath}\n` +
          readLogTail(logPath),
      );
    }
    const file = join(home, "runtime", "server.json");
    if (existsSync(file)) {
      try {
        const info = JSON.parse(readFileSync(file, "utf8"));
        if (info && info.url && info.base_url) return info;
      } catch {
        /* the server may be mid-write despite atomic rename on some filesystems */
      }
    }
    await new Promise((done) => setTimeout(done, 200));
  }
  throw new Error(`Timed out waiting for runtime/server.json. Log: ${logPath}\n${readLogTail(logPath)}`);
}

function readLogTail(file, lines = 40) {
  try {
    return readFileSync(file, "utf8").split(/\r?\n/).slice(-lines).join("\n");
  } catch {
    return "(no server log)";
  }
}

async function waitForHttp(url, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  let last = "";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
      last = `HTTP ${response.status}`;
    } catch (error) {
      last = error instanceof Error ? error.message : String(error);
    }
    await new Promise((done) => setTimeout(done, 200));
  }
  throw new Error(`Local service did not answer ${url}: ${last}`);
}

export async function startServer({ root, home, label }) {
  if ((pythonExe.includes("/") || pythonExe.includes("\\")) && !existsSync(pythonExe)) {
    throw new Error(
      `Windows Python not found at ${pythonExe}. Set MORPHD_VISUAL_PYTHON or create .venv-win.`,
    );
  }
  const logPath = join(root, `${label}.server.log`);
  const logFd = openSync(logPath, "a");
  const child = spawn(
    pythonExe,
    ["-m", "morphd", "serve", "--config-only", "--home", home],
    {
      cwd: repoRoot,
      env: serverEnv(root, home),
      stdio: ["ignore", logFd, logFd],
      windowsHide: true,
    },
  );
  try {
    const info = await waitForServerInfo(home, child, logPath);
    await waitForHttp(info.base_url);
    return {
      child,
      info,
      baseUrl: info.base_url,
      url: info.url,
      logPath,
      async stop() {
        if (child.exitCode !== null) return;
        try {
          writeFileSync(join(home, "runtime", "shell-quit"), "");
        } catch {
          /* fall through to taskkill */
        }
        const exited = await new Promise((done) => {
          const timer = setTimeout(() => done(false), 8000);
          child.once("exit", () => {
            clearTimeout(timer);
            done(true);
          });
        });
        if (!exited) {
          if (isWindows) {
            spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
              stdio: "ignore",
              windowsHide: true,
            });
          } else {
            child.kill("SIGKILL");
          }
          await new Promise((done) => {
            const timer = setTimeout(done, 3000);
            child.once("exit", () => {
              clearTimeout(timer);
              done();
            });
          });
        }
      },
    };
  } catch (error) {
    try {
      child.kill();
    } catch {
      /* ignore */
    }
    throw error;
  } finally {
    try {
      closeSync(logFd);
    } catch {
      /* ignore */
    }
  }
}

export async function markHostConnected(server, hostToken, sessionId = "sess-visual-live") {
  const response = await fetch(`${server.baseUrl}/api/host/session-start`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Morphd-Host-Token": hostToken,
    },
    body: JSON.stringify({ host: "dsh", session_id: sessionId }),
  });
  if (!response.ok) {
    throw new Error(`Could not mark the fixture host connected: HTTP ${response.status} ${await response.text()}`);
  }
}

export function cleanupRunRoot(root) {
  if (process.env.MORPHD_VISUAL_KEEP === "1") return;
  try {
    rmSync(root, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 });
  } catch {
    /* temporary files are not evidence; leaving them behind must not fail the run */
  }
}
