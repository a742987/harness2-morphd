import { strict as assert } from "node:assert";
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import test from "node:test";

const cli = fileURLToPath(new URL("../dist/index.js", import.meta.url));

function fakeEngine(dir) {
  const script = join(dir, "engine.cmd");
  const log = join(dir, "calls.log");
  writeFileSync(script, `@echo off\r\necho %*>>"${log}"\r\nexit /b 0\r\n`);
  return { script, log };
}

function run(args, engine, extraEnv = {}) {
  return spawnSync(process.execPath, [cli, ...args], {
    encoding: "utf8",
    env: { ...process.env, MORPHD_ENGINE_BIN: engine, ...extraEnv },
    timeout: 30000,
  });
}

test("version and help do not need an engine", () => {
  const version = spawnSync(process.execPath, [cli, "--version"], { encoding: "utf8" });
  assert.equal(version.status, 0);
  assert.match(version.stdout, /^0\.5\.0/);
  const help = spawnSync(process.execPath, [cli, "help"], { encoding: "utf8" });
  assert.equal(help.status, 0);
  assert.match(help.stdout, /setup/);
  assert.match(help.stdout, /never runs sudo/);
});

test("doctor invokes the engine without leaking environment assumptions", () => {
  const dir = mkdtempSync(join(tmpdir(), "morphd-cli-"));
  const { script, log } = fakeEngine(dir);
  const result = run(["doctor"], script);
  assert.equal(result.status, 0, result.stderr);
  const calls = readFileSync(log, "utf8");
  assert.match(calls, /consumer-doctor/);
  assert.match(calls, /status/);
});

test("setup passes host and project through and --no-ui exits", () => {
  const dir = mkdtempSync(join(tmpdir(), "morphd-cli-"));
  const { script, log } = fakeEngine(dir);
  const result = run(["setup", "--host", "dsh", "--project", "/tmp/project", "--no-ui"], script);
  assert.equal(result.status, 0, result.stderr);
  assert.match(readFileSync(log, "utf8"), /setup --host dsh --project \/tmp\/project/);
  assert.match(result.stdout, /setup complete/);
});

test("uninstall reads the install manifest and removes each host", () => {
  const dir = mkdtempSync(join(tmpdir(), "morphd-cli-"));
  const { script, log } = fakeEngine(dir);
  const home = join(dir, "home");
  const manifest = JSON.stringify({ version: 1, hosts: [{ host: "dsh" }, { host: "claude-code" }] });
  writeFileSync(join(dir, "install.json"), manifest);
  // The engine receives the same home, so put the manifest in it.
  mkdirSync(home);
  copyFileSync(join(dir, "install.json"), join(home, "install.json"));
  const result = run(["uninstall", "--home", home], script);
  assert.equal(result.status, 0, result.stderr);
  const calls = readFileSync(log, "utf8");
  assert.match(calls, /uninstall --host dsh/);
  assert.match(calls, /uninstall --host claude-code/);
});

test("missing platform package is actionable", () => {
  const result = spawnSync(process.execPath, [cli, "doctor"], {
    encoding: "utf8",
    env: { ...process.env, MORPHD_ENGINE_BIN: "/does/not/exist" },
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /missing file/);
});
