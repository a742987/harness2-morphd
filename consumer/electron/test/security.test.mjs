import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = new URL("../", import.meta.url);
const main = readFileSync(fileURLToPath(new URL("src/main.ts", root)), "utf8");
const preload = readFileSync(fileURLToPath(new URL("src/preload.cts", root)), "utf8");
const engine = readFileSync(fileURLToPath(new URL("src/engine.ts", root)), "utf8");

test("Electron window uses the sandbox baseline", () => {
  assert.match(main, /contextIsolation:\s*true/);
  assert.match(main, /nodeIntegration:\s*false/);
  assert.match(main, /sandbox:\s*true/);
  assert.match(main, /setPermissionRequestHandler/);
  assert.match(main, /will-navigate/);
  assert.match(main, /action:\s*"deny"/);
  assert.match(engine, /MORPHD_ENGINE_BIN|resources.*engine/);
});

test("preload exposes exactly the whitelisted API", () => {
  const methods = [...preload.matchAll(/^\s{2}([a-zA-Z]+):/gm)].map((match) => match[1]);
  assert.deepEqual(methods, [
    "getStatus",
    "showWindow",
    "openSettings",
    "pause",
    "setIncognito",
    "applyCandidate",
    "revertCandidate",
    "quitShell",
  ]);
  assert.doesNotMatch(preload, /require\(["']node:/);
  assert.doesNotMatch(preload, /fs\.|child_process|fetch\(/);
});

test("IPC channels are fixed and validated", () => {
  const channels = [...main.matchAll(/ipcMain\.handle\("([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(channels, [
    "morphd:status",
    "morphd:show",
    "morphd:open-settings",
    "morphd:pause",
    "morphd:incognito",
    "morphd:apply-candidate",
    "morphd:revert-candidate",
    "morphd:quit-shell",
  ]);
  assert.match(main, /validSender\(event\)/);
});
