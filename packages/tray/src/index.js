#!/usr/bin/env node
/**
 * Optional tray helper for Windows 10/11 x64. It only attaches to an already-running
 * host server and exits when that server disappears. It never starts a host, a daemon,
 * or a browser.
 */
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);
const PLATFORM_PACKAGE = "@harness2-morphd/win32-x64";

if (process.platform !== "win32" || process.arch !== "x64") {
  process.stderr.write(
    `morphd-tray: Windows 10/11 x64 only; this is ${process.platform}-${process.arch}.\n`,
  );
  process.exit(1);
}

function platformPackage() {
  return PLATFORM_PACKAGE;
}

function engine() {
  const explicit = process.env.MORPHD_ENGINE_BIN?.trim();
  if (explicit) return { command: explicit, prefix: [] };
  try {
    const manifest = require.resolve(`${platformPackage()}/package.json`);
    const pkg = JSON.parse(readFileSync(manifest, "utf8"));
    if (pkg.morphd?.engine) {
      const target = join(dirname(manifest), pkg.morphd.engine);
      if (existsSync(target)) {
        const sumsFile = join(dirname(manifest), "SHA256SUMS");
        const relative = String(pkg.morphd.engine).replace(/\\/gu, "/");
        if (existsSync(sumsFile)) {
          const wanted = readFileSync(sumsFile, "utf8")
            .split(/\r?\n/u)
            .map((line) => line.trim().split(/\s+/u))
            .find((parts) => parts.length >= 2 && parts[parts.length - 1].replace(/^\.\//u, "") === relative);
          const actual = createHash("sha256").update(readFileSync(target)).digest("hex");
          if (!wanted || actual !== wanted[0].toLowerCase()) {
            process.stderr.write("morphd-tray: platform engine checksum mismatch; refusing to run.\n");
            process.exit(1);
          }
        } else if (process.env.MORPHD_ALLOW_UNVERIFIED_ENGINE !== "1") {
          process.stderr.write("morphd-tray: platform engine has no SHA256SUMS; refusing to run.\n");
          process.exit(1);
        }
        return { command: target, prefix: [] };
      }
    }
  } catch {
    /* optional platform package missing */
  }
  const explicitPython = process.env.MORPHD_PYTHON?.trim();
  if (!explicitPython && process.env.MORPHD_ALLOW_PYTHON_ENGINE !== "1") {
    process.stderr.write(
      `morphd-tray: no verified engine found for Windows x64; ` +
        `install ${platformPackage()} or set MORPHD_ENGINE_BIN.\n`,
    );
    process.exit(1);
  }
  const candidates = [
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
  process.stderr.write(
    `morphd-tray: no engine found for Windows x64; ` +
      `install ${platformPackage()} or set MORPHD_ENGINE_BIN.\n`,
  );
  process.exit(1);
}

const home = process.env.MORPHD_HOME?.trim() || join(homedir(), ".morphd");

function pidAlive(pid) {
  if (!Number.isFinite(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function runningServer() {
  const pointer = join(home, "runtime", "server.json");
  try {
    if (existsSync(pointer)) {
      const info = JSON.parse(readFileSync(pointer, "utf8"));
      if (info.base_url && info.host_token && (!info.pid || pidAlive(info.pid))) return info;
    }
  } catch {
    /* stale pointer */
  }
  const servers = join(home, "runtime", "servers");
  if (!existsSync(servers)) return null;
  try {
    const candidates = readdirSync(servers)
      .filter((name) => name.endsWith(".json"))
      .map((name) => {
        try {
          return JSON.parse(readFileSync(join(servers, name), "utf8"));
        } catch {
          return null;
        }
      })
      .filter((info) => info && info.base_url && info.host_token && (!info.pid || pidAlive(info.pid)))
      .sort((a, b) => String(b.started_at || "").localeCompare(String(a.started_at || "")));
    return candidates[0] || null;
  } catch {
    return null;
  }
}

if (!runningServer()) {
  process.stderr.write("morphd-tray: no running host server; tray is not resident.\n");
  process.exit(0);
}

const spec = engine();
const args = [...spec.prefix, "tray", "--home", home];
const batch = /\.(cmd|bat)$/iu.test(spec.command);
const child = spawn(
  batch ? process.env.ComSpec || "cmd.exe" : spec.command,
  batch ? ["/d", "/s", "/c", spec.command, ...args] : args,
  { stdio: "inherit" },
);
const forward = (signal) => {
  try {
    child.kill(signal);
  } catch {
    /* already gone */
  }
};
process.on("SIGINT", () => forward("SIGINT"));
process.on("SIGTERM", () => forward("SIGTERM"));
child.on("exit", (code) => process.exit(code ?? 0));
