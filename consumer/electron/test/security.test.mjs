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
    "pickFolder",
    "pause",
    "setIncognito",
    "applyCandidate",
    "revertCandidate",
    "quitShell",
  ]);
  const invoked = [...preload.matchAll(/ipcRenderer\.invoke\(\s*"([^"]+)"/g)].map(
    (match) => match[1],
  );
  assert.deepEqual(invoked, [
    "morphd:status",
    "morphd:show",
    "morphd:open-settings",
    "morphd:pick-folder",
    "morphd:pause",
    "morphd:incognito",
    "morphd:apply-candidate",
    "morphd:revert-candidate",
    "morphd:quit-shell",
  ]);
  assert.match(
    preload,
    /pickFolder:\s*\(\s*kind:\s*string\s*\)\s*=>\s*ipcRenderer\.invoke\(\s*"morphd:pick-folder"\s*,\s*String\(kind\)\s*\)/,
  );
  assert.doesNotMatch(preload, /exposeInMainWorld\(\s*["']ipcRenderer/);
  assert.doesNotMatch(preload, /require\(["']node:/);
  assert.doesNotMatch(preload, /fs\.|child_process|fetch\(/);
});

test("IPC channels are fixed and validated", () => {
  const channels = [...main.matchAll(/ipcMain\.handle\("([^"]+)"/g)].map((match) => match[1]);
  assert.deepEqual(channels, [
    "morphd:status",
    "morphd:show",
    "morphd:open-settings",
    "morphd:pick-folder",
    "morphd:pause",
    "morphd:incognito",
    "morphd:apply-candidate",
    "morphd:revert-candidate",
    "morphd:quit-shell",
  ]);
  assert.match(main, /validSender\(event\)/);
});

test("engine resolution verifies checksums and never silently runs Python", () => {
  assert.match(engine, /checksum mismatch/);
  assert.match(engine, /MORPHD_ALLOW_PYTHON_ENGINE/);
  assert.match(engine, /loopbackBase/);
  assert.match(engine, /function validServer/);
  assert.match(main, /Boolean\(frameOrigin && expected && frameOrigin === expected\)/);
  assert.match(main, /const info = readServer\(home\)/);
});

test("folder picker validates kind and uses the native directory dialog", () => {
  assert.match(
    main,
    /ipcMain\.handle\(\s*"morphd:pick-folder"[\s\S]*?validSender\(event\)/,
  );
  assert.match(
    main,
    /PICK_FOLDER_KINDS\s*=\s*\[\s*"install"\s*,\s*"config"\s*,\s*"project"\s*,?\s*\]/,
  );
  assert.match(main, /isPickFolderKind\(kind\)/);
  assert.match(main, /typeof value === "string"/);
  assert.match(
    main,
    /dialog\.showOpenDialog\(\s*mainWindow\s*,\s*\{[\s\S]*?title:\s*PICK_FOLDER_TITLES\[kind\][\s\S]*?properties:\s*\[\s*"openDirectory"\s*\]/,
  );
  assert.match(main, /result\.canceled[\s\S]*?path:\s*""/);
  assert.match(main, /\{\s*path:\s*result\.filePaths\[0\]\s*\}/);
});
