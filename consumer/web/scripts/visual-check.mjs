#!/usr/bin/env node
// Visual smoke check against a running local morphd service.
// Usage: URL='http://127.0.0.1:PORT/#bootstrap=...' EDGE='C:\...\msedge.exe' \
//        OUT='D:\...\docs\evidence\ui' node scripts/visual-check.mjs
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
let axePath = "";
try {
  axePath = require.resolve("axe-core/axe.min.js");
} catch {
  /* optional accessibility audit */
}

const url = process.argv[2] || process.env.URL;
const edge = process.env.EDGE || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const out = process.argv[3] || process.env.OUT || ".";
const mode = process.argv[4] || "all";
if (!url) {
  console.error("URL is required");
  process.exit(1);
}
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({ executablePath: edge, headless: true });
const errors = [];

async function shot(name, options, actions) {
  const context = await browser.newContext({
    viewport: options.mobile ? { width: 375, height: 900 } : { width: 1440, height: 1100 },
    deviceScaleFactor: options.scale || 1,
    colorScheme: options.colorScheme,
    locale: "zh-CN",
  });
  const page = await context.newPage();
  if (axePath) await page.addInitScript({ path: axePath });
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`${name}: console: ${message.text()}`);
  });
  page.on("pageerror", (error) => errors.push(`${name}: pageerror: ${error.message}`));
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForSelector(".hero-main, .onboarding-main", { timeout: 15000 });
  if (actions) await actions(page);
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: options.fullPage !== false });
  if (axePath) {
    const result = await page.evaluate(async () => {
      const api = globalThis.axe;
      if (!api) return { violations: [] };
      const scan = await api.run(document, { resultTypes: ["violations"] });
      return {
        violations: scan.violations
          .filter((item) => item.impact === "serious" || item.impact === "critical")
          .map((item) => `${item.id}: ${item.help} ${JSON.stringify(item.nodes.map((node) => `${node.target}:${(node.html || "").slice(0, 180)}`))}`),
      };
    });
    for (const violation of result.violations) errors.push(`${name}: a11y: ${violation}`);
  }
  await context.close();
}

const pages = [
  ["hosts", 1],
  ["models", 2],
  ["learning", 3],
  ["budget", 4],
  ["history", 5],
  ["privacy", 6],
  ["diagnostics", 7],
  ["help", 8],
  ["advanced", 9],
];

if (mode === "onboarding") {
  await shot("onboarding-host-light", { colorScheme: "light" });
  await shot("onboarding-model-light", { colorScheme: "light" }, async (page) => {
    await page.getByRole("button", { name: /稍后再说|Later/ }).click();
    await page.waitForTimeout(250);
  });
  await shot("onboarding-budget-light", { colorScheme: "light" }, async (page) => {
    await page.getByRole("button", { name: /稍后再说|Later/ }).click();
    await page.waitForTimeout(250);
    await page.getByRole("button", { name: /稍后配置|Later/ }).click();
    await page.waitForTimeout(250);
  });
} else {
  await shot("home-light", { colorScheme: "light" });
  await shot("home-dark", { colorScheme: "dark" });
  await shot("home-mobile", { colorScheme: "light", mobile: true });
  await shot("home-125", { colorScheme: "light", scale: 1.25 });
  await shot("home-150", { colorScheme: "light", scale: 1.5 });
  await shot("home-xl-high", { colorScheme: "light" }, async (page) => {
    await page.evaluate(() => {
      document.documentElement.dataset.font = "xlarge";
      document.documentElement.dataset.contrast = "high";
    });
    await page.waitForTimeout(200);
  });
  for (const [name, index] of pages) {
    await shot(`${name}-light`, { colorScheme: "light" }, async (page) => {
      await page.locator(".nav-item").nth(index).click();
      await page.waitForTimeout(300);
    });
  }
  await shot("candidate-modal-light", { colorScheme: "light" }, async (page) => {
    await page.locator(".nav-item").nth(5).click();
    await page.waitForTimeout(300);
    await page.getByRole("button", { name: /查看 diff 与原因|View diff and reason/ }).first().click();
    await page.waitForSelector(".modal-panel");
    await page.waitForTimeout(500);
  });
}

await browser.close();
if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log("visual smoke passed");
