#!/usr/bin/env node
/**
 * Optional tray helper. It only attaches to an already-running host server and exits
 * when that server disappears. It never starts a host, a daemon, or a browser.
 */
import { spawn, spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import process from "node:process";

const require = createRequire(import.meta.url);

function platformPackage() {
  return `@harness2-morphd/${process.platform}-${process.arch}`;
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
  for (const python of [process.env.MORPHD_PYTHON, "python3", "python"].filter(Boolean)) {
    const probe = spawnSync(python, ["-c", "import morphd"], { stdio: "ignore", timeout: 5000 });
    if (probe.status === 0) return { command: python, prefix: ["-m", "morphd"] };
  }
  process.stderr.write(
    `morphd-tray: no engine found for ${process.platform}-${process.arch}; ` +
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
const child = spawn(spec.command, args, { stdio: "inherit" });
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
