#!/usr/bin/env node
/**
 * @harness2-morphd/cli — thin, auditable launcher.
 *
 * Windows 10/11 x64 only. It deliberately has no postinstall scripts and never
 * downloads a binary from an arbitrary URL. The engine comes from the win32-x64
 * platform package, an explicit MORPHD_ENGINE_BIN, or (development only)
 * `python -m morphd`.
 */
import { spawn, spawnSync, type SpawnOptions } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync, readFileSync } from "node:fs";
import { homedir, release } from "node:os";
import { dirname, join } from "node:path";
import process from "node:process";

const VERSION = "0.5.0";
const MIN_NODE = 20;
const PLATFORM_PACKAGE = "@harness2-morphd/win32-x64";
const require = createRequire(import.meta.url);

interface EngineCommand {
  command: string;
  prefix: string[];
  source: string;
}

function nodeMajor(): number {
  return Number(process.versions.node.split(".")[0]);
}

function assertNode(): void {
  if (nodeMajor() < MIN_NODE) {
    fail(`harness2-morphd needs Node >= ${MIN_NODE}; this is ${process.versions.node}.`);
  }
}

function assertPlatform(): void {
  if (process.platform !== "win32" || process.arch !== "x64") {
    fail(
      `harness2-morphd supports Windows 10/11 x64 only; this is ${process.platform}-${process.arch}.`,
      "PLATFORM_UNSUPPORTED",
    );
  }
  const major = Number(release().split(".")[0]);
  if (!Number.isFinite(major) || major < 10) {
    fail(`harness2-morphd requires Windows 10 or 11; this is Windows ${release()}.`, "PLATFORM_UNSUPPORTED");
  }
}

function platformPackage(): string {
  return PLATFORM_PACKAGE;
}

function engineFromPlatformPackage(): EngineCommand | null {
  const name = platformPackage();
  try {
    const packageJson = require.resolve(`${name}/package.json`);
    const pkg = JSON.parse(readFileSync(packageJson, "utf8")) as {
      morphd?: { engine?: string };
    };
    if (pkg.morphd?.engine) {
      const target = join(dirname(packageJson), pkg.morphd.engine);
      if (existsSync(target)) {
        return { command: target, prefix: [], source: name };
      }
    }
  } catch {
    /* optional package not installed for this platform */
  }
  return null;
}

function pythonEngine(): EngineCommand | null {
  const explicit = process.env.MORPHD_PYTHON?.trim();
  const candidates: { command: string; prefix: string[] }[] = [
    ...(explicit ? [{ command: explicit, prefix: [] as string[] }] : []),
    { command: "python", prefix: [] },
    { command: "py", prefix: ["-3"] },
  ];
  for (const candidate of candidates) {
    const probe = spawnSync(candidate.command, [...candidate.prefix, "-c", "import morphd"], {
      stdio: "ignore",
      timeout: 5000,
    });
    if (probe.status === 0) {
      return {
        command: candidate.command,
        prefix: [...candidate.prefix, "-m", "morphd"],
        source: [candidate.command, ...candidate.prefix, "-m morphd"].join(" "),
      };
    }
  }
  return null;
}

function resolveEngine(): EngineCommand {
  const explicit = process.env.MORPHD_ENGINE_BIN?.trim();
  if (explicit) {
    if (!existsSync(explicit)) {
      fail(`MORPHD_ENGINE_BIN points to a missing file: ${explicit}`, "ENGINE_MISSING");
    }
    return { command: explicit, prefix: [], source: "MORPHD_ENGINE_BIN" };
  }
  const platform = engineFromPlatformPackage();
  if (platform) return platform;
  const python = pythonEngine();
  if (python) return python;
  fail(
    `No harness2-morphd engine found for Windows x64. ` +
      `Install the ${PLATFORM_PACKAGE} platform package or set MORPHD_ENGINE_BIN.`,
    "ENGINE_MISSING",
  );
}

function expandedCommand(engine: EngineCommand, args: string[]): { file: string; argv: string[] } {
  const all = [...engine.prefix, ...args];
  if (/\.(cmd|bat)$/iu.test(engine.command)) {
    // .cmd/.bat cannot be spawned directly without a shell. Keep argv entries separate;
    // cmd.exe treats a joined, quoted string as the literal command name.
    return {
      file: process.env.ComSpec || "cmd.exe",
      argv: ["/d", "/s", "/c", engine.command, ...all],
    };
  }
  return { file: engine.command, argv: all };
}

function runEngine(engine: EngineCommand, args: string[], options: SpawnOptions & { capture?: boolean } = {}): number {
  const { file, argv } = expandedCommand(engine, args);
  if (options.capture) {
    const result = spawnSync(file, argv, {
      ...options,
      encoding: "utf8",
      stdio: ["inherit", "pipe", "pipe"],
    });
    if (result.stdout) process.stdout.write(result.stdout);
    if (result.stderr) process.stderr.write(result.stderr);
    return result.status ?? 1;
  }
  const result = spawnSync(file, argv, {
    stdio: "inherit",
    ...options,
  });
  return result.status ?? 1;
}

function spawnEngine(engine: EngineCommand, args: string[], options: SpawnOptions = {}) {
  const { file, argv } = expandedCommand(engine, args);
  return spawn(file, argv, {
    stdio: "inherit",
    ...options,
  });
}

function fail(message: string, code = "MORPHD_ERROR"): never {
  process.stderr.write(`morphd: ${code}: ${message}\n`);
  process.exit(1);
}

function parseArgs(argv: string[]): { command: string; args: string[] } {
  const command = argv[0] || "help";
  return { command, args: argv.slice(1) };
}

function homeArgs(args: string[]): { home: string | null; rest: string[] } {
  let home: string | null = process.env.MORPHD_HOME?.trim() || null;
  const rest: string[] = [];
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === "--home" && args[index + 1]) {
      home = args[index + 1];
      index += 1;
    } else if (args[index].startsWith("--home=")) {
      home = args[index].slice("--home=".length);
    } else {
      rest.push(args[index]);
    }
  }
  return { home, rest };
}

function engineEnv(home: string | null): NodeJS.ProcessEnv {
  return home ? { ...process.env, MORPHD_HOME: home } : { ...process.env };
}

function readInstall(home: string): { hosts?: { host: string }[] } | null {
  try {
    return JSON.parse(readFileSync(join(home, "install.json"), "utf8")) as {
      hosts?: { host: string }[];
    };
  } catch {
    return null;
  }
}

function effectiveHome(home: string | null): string {
  return home || process.env.MORPHD_HOME?.trim() || join(homedir(), ".morphd");
}

function help(): void {
  process.stdout.write(`morphd ${VERSION} (Windows 10/11 x64)

Usage:
  npx @harness2-morphd/cli@latest setup [--host dsh|claude-code|codex] [--project DIR]
  npx @harness2-morphd/cli@latest doctor
  npx @harness2-morphd/cli@latest status
  npx @harness2-morphd/cli@latest update [--yes]
  npx @harness2-morphd/cli@latest uninstall [--host KIND] [--delete-data]
  npx @harness2-morphd/cli@latest serve [--open]
  npx @harness2-morphd/cli@latest host <run|event> ...

Environment:
  MORPHD_HOME          data directory (default %USERPROFILE%\\.morphd)
  MORPHD_ENGINE_BIN    explicit engine binary
  MORPHD_PYTHON        Python interpreter for development mode

The launcher never runs sudo, curl | sh, or arbitrary postinstall scripts.
`);
}

function cmdSetup(args: string[]): number {
  assertNode();
  const engine = resolveEngine();
  const { home, rest } = homeArgs(args);
  const noUi = rest.includes("--no-ui");
  const filtered = rest.filter((item) => item !== "--no-ui");
  process.stderr.write(`morphd: engine ${engine.source}\n`);
  const status = runEngine(engine, ["setup", ...filtered], { env: engineEnv(home) });
  if (status !== 0) return status;
  if (noUi) {
    process.stdout.write("morphd: setup complete. Start the UI with `morphd serve --open`.\n");
    return 0;
  }
  process.stdout.write(
    "morphd: setup complete. Opening the local UI for the three-step guide.\n" +
      "        Finish the guide, then press Ctrl+C to close the temporary installer UI.\n",
  );
  return runEngine(engine, ["serve", "--config-only", "--open"], { env: engineEnv(home) });
}

function cmdDoctor(args: string[]): number {
  assertNode();
  const { home, rest } = homeArgs(args);
  process.stdout.write(`morphd launcher ${VERSION}\n`);
  process.stdout.write(`node ${process.versions.node} (${process.platform}-${process.arch})\n`);
  process.stdout.write(`platform package ${platformPackage()}\n`);
  let engine: EngineCommand;
  try {
    engine = resolveEngine();
  } catch (error) {
    process.stderr.write(`${(error as Error).message}\n`);
    return 1;
  }
  process.stdout.write(`engine ${engine.source}\n`);
  const code = runEngine(engine, ["consumer-doctor", ...rest], { env: engineEnv(home), capture: true });
  const status = runEngine(engine, ["status", ...rest], { env: engineEnv(home), capture: true });
  if (code !== 0 || status !== 0) return 1;
  process.stdout.write("doctor: checks complete\n");
  return 0;
}

function cmdUpdate(args: string[]): number {
  assertNode();
  const yes = args.includes("--yes");
  const npm = "npm.cmd";
  const view = spawnSync(npm, ["view", "@harness2-morphd/cli", "version"], {
    encoding: "utf8",
    timeout: 30000,
  });
  if (view.status !== 0) {
    fail("cannot reach the npm registry; update was not attempted.", "UPDATE_FAILED");
  }
  const latest = (view.stdout || "").trim();
  if (!latest) fail("npm returned no version.", "UPDATE_FAILED");
  if (latest === VERSION) {
    process.stdout.write(`harness2-morphd is up to date (${VERSION}).\n`);
    return 0;
  }
  process.stdout.write(`morphd ${VERSION} → ${latest} is available.\n`);
  process.stdout.write("This launcher never updates silently. Review the release notes first.\n");
  if (!yes) {
    process.stdout.write("Run with --yes to install the global update, or use `npx @harness2-morphd/cli@latest`.\n");
    return 0;
  }
  const install = spawnSync(npm, ["install", "-g", `@harness2-morphd/cli@${latest}`], {
    stdio: "inherit",
    timeout: 300000,
  });
  return install.status ?? 1;
}

function cmdUninstall(args: string[]): number {
  assertNode();
  const engine = resolveEngine();
  const { home, rest } = homeArgs(args);
  const deleteData = rest.includes("--delete-data");
  const hostIndex = rest.indexOf("--host");
  const hosts: string[] = [];
  if (hostIndex >= 0 && rest[hostIndex + 1]) {
    hosts.push(rest[hostIndex + 1]);
  } else {
    const install = readInstall(effectiveHome(home));
    for (const item of install?.hosts || []) hosts.push(item.host);
  }
  if (hosts.length === 0) {
    process.stdout.write("morphd: no adapted hosts found.\n");
  }
  let code = 0;
  for (const host of hosts) {
    const status = runEngine(
      engine,
      ["uninstall", "--host", host, ...(deleteData ? ["--delete-data"] : [])],
      { env: engineEnv(home) },
    );
    code = code || status;
  }
  if (deleteData && hosts.length === 0) {
    const status = runEngine(engine, ["privacy", "delete", "--scope", "all"], { env: engineEnv(home) });
    code = code || status;
  }
  return code;
}

function cmdServe(args: string[]): number {
  assertNode();
  const engine = resolveEngine();
  const { home, rest } = homeArgs(args);
  const child = spawnEngine(engine, ["serve", ...rest], { env: engineEnv(home) });
  const forward = (signal: NodeJS.Signals) => {
    try {
      child.kill(signal);
    } catch {
      /* already gone */
    }
  };
  process.on("SIGINT", () => forward("SIGINT"));
  process.on("SIGTERM", () => forward("SIGTERM"));
  child.on("exit", (code, signal) => {
    if (signal) process.exit(0);
    process.exit(code ?? 0);
    });
  return 0;
}

function cmdPassthrough(args: string[]): number {
  assertNode();
  const engine = resolveEngine();
  const { home, rest } = homeArgs(args);
  return runEngine(engine, rest, { env: engineEnv(home) });
}

function main(): number {
  const { command, args } = parseArgs(process.argv.slice(2));
  const runtimeCommands = new Set([
    "setup",
    "doctor",
    "update",
    "uninstall",
    "serve",
    "status",
    "host",
    "privacy",
  ]);
  if (runtimeCommands.has(command)) assertPlatform();
  switch (command) {
    case "setup":
      return cmdSetup(args);
    case "doctor":
      return cmdDoctor(args);
    case "update":
      return cmdUpdate(args);
    case "uninstall":
      return cmdUninstall(args);
    case "serve":
      return cmdServe(args);
    case "status":
    case "host":
    case "privacy":
      return cmdPassthrough([command, ...args]);
    case "version":
    case "--version":
    case "-v":
      process.stdout.write(`${VERSION}\n`);
      return 0;
    case "help":
    case "--help":
    case "-h":
    default:
      help();
      return 0;
  }
}

try {
  process.exitCode = main();
} catch (error) {
  fail(error instanceof Error ? error.message : String(error));
}
