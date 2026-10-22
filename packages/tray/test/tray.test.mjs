import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import test from "node:test";

const source = readFileSync(fileURLToPath(new URL("../src/index.js", import.meta.url)), "utf8");
const pkg = JSON.parse(readFileSync(fileURLToPath(new URL("../package.json", import.meta.url)), "utf8"));

test("tray helper attaches to an existing server and does not start a host", () => {
  assert.match(source, /server\.json/);
  assert.doesNotMatch(source, /spawn[^]*?host run/);
  assert.doesNotMatch(source, /postinstall/);
  assert.equal(pkg.scripts?.postinstall, undefined);
});

test("tray helper degrades when no server is running", () => {
  assert.match(source, /no running host server/);
  assert.match(source, /process\.exit\(0\)/);
});
