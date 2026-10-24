import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
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

function loopbackBase(url: string): boolean {
  try {
    const parsed = new URL(url);
    return (
      parsed.protocol === "http:" &&
      ["127.0.0.1", "localhost", "::1"].includes(parsed.hostname)
    );
  } catch {
    return false;
  }
}

function validServer(payload: ServerInfo): boolean {
  return Boolean(payload.base_url && payload.url && payload.host_token && loopbackBase(payload.base_url) && loopbackBase(payload.url));
}

export function readServer(home = homeDir()): ServerInfo | null {
  const runtime = join(home, "runtime");
  const pointer = join(runtime, "server.json");
  try {
    if (existsSync(pointer)) {
      const payload = JSON.parse(readFileSync(pointer, "utf8")) as ServerInfo;
      if (validServer(payload) && (payload.pid === undefined || processAlive(payload.pid))) return payload;
    }
  } catch {
    /* stale pointer */
  }
  const servers = join(runtime, "servers");
  if (!existsSync(servers)) return null;
  try {
    const candidates = readdirSync(servers)
      .filter((name) => name.endsWith(".json"))
      .map((name) => {
        try {
          return JSON.parse(readFileSync(join(servers, name), "utf8")) as ServerInfo;
        } catch {
          return null;
        }
      })
      .filter((item): item is ServerInfo => Boolean(item && validServer(item) && (item.pid === undefined || processAlive(item.pid))))
      .sort((a, b) => String(b.started_at).localeCompare(String(a.started_at)));
    return candidates[0] || null;
  } catch {
    return null;
  }
}

function processAlive(pid: number): boolean {
  if (!Number.isFinite(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
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
        const sums = join(bundledRoot, "engine", "SHA256SUMS");
        if (!existsSync(sums)) {
          if (process.env.MORPHD_ALLOW_UNVERIFIED_ENGINE !== "1") {
            throw new Error("bundled engine checksum manifest is missing; refusing to run it");
          }
        } else {
          const wanted = readFileSync(sums, "utf8")
            .split(/\r?\n/u)
            .map((line) => line.trim().split(/\s+/u))
            .find(
              (parts) =>
                parts.length >= 2 &&
                parts[parts.length - 1].replace(/^\.\//u, "") === "morphd-engine.exe",
            );
          const actual = createHash("sha256").update(readFileSync(candidate)).digest("hex");
          if (!wanted || actual !== wanted[0].toLowerCase()) {
            throw new Error("bundled engine checksum mismatch; refusing to run it");
          }
        }
        return { command: candidate, prefix: [] };
      }
    } catch (error) {
      if (error instanceof Error && error.message.includes("checksum")) throw error;
      /* ignore inaccessible candidates */
    }
  }
  try {
    const manifest = require.resolve(`${PLATFORM_PACKAGE}/package.json`);
    const pkg = JSON.parse(readFileSync(manifest, "utf8")) as { morphd?: { engine?: string } };
    if (pkg.morphd?.engine) {
      const file = join(dirname(manifest), pkg.morphd.engine);
      if (existsSync(file)) {
        verifyEngineChecksum(manifest, file, String(pkg.morphd.engine).replace(/\\/gu, "/"));
        return { command: file, prefix: [] };
      }
    }
  } catch (error) {
    if (error instanceof Error && error.message.startsWith("engine checksum")) throw error;
    /* optional package not installed */
  }
  const explicitPython = process.env.MORPHD_PYTHON?.trim();
  if (!explicitPython && process.env.MORPHD_ALLOW_PYTHON_ENGINE !== "1") {
    throw new Error(
      "No verified harness2-morphd engine found for Windows x64. " +
        `Install ${PLATFORM_PACKAGE} or set MORPHD_ENGINE_BIN. ` +
        "Python source fallback is development-only (MORPHD_ALLOW_PYTHON_ENGINE=1).",
    );
  }
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

function verifyEngineChecksum(manifest: string, target: string, relative: string): void {
  const sums = join(dirname(manifest), "SHA256SUMS");
  if (!existsSync(sums)) {
    if (process.env.MORPHD_ALLOW_UNVERIFIED_ENGINE === "1") return;
    throw new Error("engine checksum manifest is missing; refusing to run the platform binary");
  }
  const wanted = readFileSync(sums, "utf8")
    .split(/\r?\n/u)
    .map((line) => line.trim().split(/\s+/u))
    .find((parts) => parts.length >= 2 && parts[parts.length - 1].replace(/^\.\//u, "") === relative);
  if (!wanted) throw new Error(`engine checksum entry missing for ${relative}`);
  const actual = createHash("sha256").update(readFileSync(target)).digest("hex");
  if (actual !== wanted[0].toLowerCase()) {
    throw new Error(`engine checksum mismatch for ${relative}; refusing to run a modified platform binary`);
  }
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
    signal: AbortSignal.timeout(10000),
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
