import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const PLATFORM_PACKAGE = "@harness2-morphd/win32-x64";

function assertWindowsX64(): void {
  if (process.platform !== "win32" || process.arch !== "x64") {
    throw new Error(
      `harness2-morphd supports Windows 10/11 x64 only; this is ${process.platform}-${process.arch}.`,
    );
  }
}

export interface ServerInfo {
  base_url: string;
  url: string;
  host_token: string;
  port: number;
  pid: number;
  started_at: string;
}

export interface InstallHost {
  host: string;
  wrapper: string;
  version: string;
  status: string;
  integration: string;
}

export interface EngineSpec {
  command: string;
  prefix: string[];
}

export function homeDir(): string {
  return process.env.MORPHD_HOME?.trim() || join(homedir(), ".morphd");
}

export function readServer(home = homeDir()): ServerInfo | null {
  const file = join(home, "runtime", "server.json");
  if (!existsSync(file)) return null;
  try {
    const payload = JSON.parse(readFileSync(file, "utf8")) as ServerInfo;
    if (!payload.base_url || !payload.host_token) return null;
    return payload;
  } catch {
    return null;
  }
}

export function waitForServer(home: string, timeoutMs = 15000): Promise<ServerInfo> {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const timer = setInterval(() => {
      const info = readServer(home);
      if (info) {
        clearInterval(timer);
        resolve(info);
        return;
      }
      if (Date.now() - started > timeoutMs) {
        clearInterval(timer);
        reject(new Error("harness2-morphd UI server did not start"));
      }
    }, 250);
  });
}

export function readInstall(home = homeDir()): { hosts?: InstallHost[] } | null {
  try {
    return JSON.parse(readFileSync(join(home, "install.json"), "utf8")) as {
      hosts?: InstallHost[];
    };
  } catch {
    return null;
  }
}

export function findEngine(): EngineSpec {
  assertWindowsX64();
  const explicit = process.env.MORPHD_ENGINE_BIN?.trim();
  if (explicit) {
    if (!existsSync(explicit)) {
      throw new Error(`MORPHD_ENGINE_BIN points to a missing file: ${explicit}`);
    }
    return { command: explicit, prefix: [] };
  }
  const bundledRoot = join(process.resourcesPath || "", "resources");
  const bundledCandidates = [
    join(bundledRoot, "engine", "morphd-engine.exe"),
    join(bundledRoot, "engine"),
  ];
  for (const candidate of bundledCandidates) {
    try {
      if (existsSync(candidate) && statSync(candidate).isFile()) {
        return { command: candidate, prefix: [] };
      }
    } catch {
      /* ignore inaccessible candidates */
    }
  }
  try {
    const manifest = require.resolve(`${PLATFORM_PACKAGE}/package.json`);
    const pkg = JSON.parse(readFileSync(manifest, "utf8")) as { morphd?: { engine?: string } };
    if (pkg.morphd?.engine) {
      const path = join(dirname(manifest), pkg.morphd.engine);
      if (existsSync(path)) return { command: path, prefix: [] };
    }
  } catch {
    /* optional package not installed */
  }
  const explicitPython = process.env.MORPHD_PYTHON?.trim();
  const candidates: EngineSpec[] = [
    ...(explicitPython ? [{ command: explicitPython, prefix: [] }] : []),
    { command: "python", prefix: [] },
    { command: "py", prefix: ["-3"] },
  ];
  for (const candidate of candidates) {
    const probe = spawnSync(candidate.command, [...candidate.prefix, "-c", "import morphd"], {
      stdio: "ignore",
      timeout: 5000,
    });
    if (probe.status === 0) {
      return { command: candidate.command, prefix: [...candidate.prefix, "-m", "morphd"] };
    }
  }
  throw new Error(
    "No harness2-morphd engine found for Windows x64. " +
      `Looked in ${bundledCandidates.join(", ")}. ` +
      `Install ${PLATFORM_PACKAGE} or set MORPHD_ENGINE_BIN.`,
  );
}

export function spawnEngine(args: string[], home = homeDir()): ChildProcess {
  const engine = findEngine();
  const child = spawn(engine.command, [...engine.prefix, ...args], {
    env: { ...process.env, MORPHD_HOME: home },
    stdio: "ignore",
  });
  child.on("error", (error) => {
    console.error("harness2-morphd engine spawn failed:", engine.command, error);
  });
  return child;
}

export function spawnHostWrapper(
  host: InstallHost,
  home = homeDir(),
): ChildProcess {
  assertWindowsX64();
  const batch = /\.(cmd|bat)$/iu.test(host.wrapper);
  const command = batch ? process.env.ComSpec || "cmd.exe" : host.wrapper;
  const args = batch ? ["/d", "/s", "/c", host.wrapper] : [];
  return spawn(command, args, {
    env: { ...process.env, MORPHD_HOME: home },
    stdio: "ignore",
  });
}

export async function api(
  info: ServerInfo,
  method: string,
  path: string,
  body?: unknown,
): Promise<Record<string, unknown>> {
  const response = await fetch(info.base_url + path, {
    method,
    headers: {
      "Content-Type": "application/json",
      "X-Morphd-Host-Token": info.host_token,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const payload = text ? (JSON.parse(text) as Record<string, unknown>) : {};
  if (!response.ok) {
    const error = (payload.error as { message?: string } | undefined)?.message || `HTTP ${response.status}`;
    throw new Error(error);
  }
  return payload;
}

export function requestStopLearning(home = homeDir()): void {
  const marker = join(home, "runtime", "shell-quit");
  try {
    writeFileSync(marker, new Date().toISOString(), { mode: 0o600 });
  } catch {
    /* the UI will show the host disconnected when the server stops */
  }
}
