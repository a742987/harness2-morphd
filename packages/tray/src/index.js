#!/usr/bin/env node
/**
 * Optional tray helper for Windows 10/11 x64. It only attaches to an already-running
 * host server and exits when that server disappears. It never starts a host, a daemon,
 * or a browser.
 */
import { spawn, spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, readFileSync } from "node:fs";
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
      if (existsSync(target)) return { command: target, prefix: [] };
    }
  } catch {
    /* optional platform package missing */
  }
  const explicitPython = process.env.MORPHD_PYTHON?.trim();
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
const serverFile = join(home, "runtime", "server.json");
if (!existsSync(serverFile)) {
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
