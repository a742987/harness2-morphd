#!/usr/bin/env node
// Visual + accessibility evidence matrix for the harness2-morphd Web UI.
//
// Reproducible run (starts a temporary seeded local service, Edge, one browser):
//   npm --prefix consumer/web run build
//   npm --prefix consumer/web run test:visual:matrix
//
// Existing single-service mode is still available for ad-hoc smoke checks:
//   URL='http://127.0.0.1:PORT/#bootstrap=...' OUT='docs/evidence/ui' node scripts/visual-check.mjs
//
// The matrix captures light/dark pages, 100/125/150% scale, mobile, XL/high-contrast,
// onboarding, the candidate modal, keyboard-only flows, and an axe report. Any serious or
// critical axe violation, console/page error, or keyboard failure exits non-zero.
import { chromium } from "playwright-core";
import { createRequire } from "node:module";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  cleanupRunRoot,
  makeRunRoot,
  markHostConnected,
  repoRoot,
  resolveEdge,
  seedVisualHome,
  startServer,
} from "./visual-fixtures.mjs";

const require = createRequire(import.meta.url);
const scriptDir = dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const serve = argv.includes("--serve");
const positional = argv.filter((item) => !item.startsWith("--") && !isFlagValue(item));
const flag = (name, fallback = "") => {
  const index = argv.indexOf(name);
  return index >= 0 ? argv[index + 1] || fallback : fallback;
};
function isFlagValue(value) {
  const index = argv.indexOf(value);
  return index > 0 && ["--edge", "--out", "--mode", "--report", "--summary"].includes(argv[index - 1]);
}

const urlArg = process.env.URL || positional[0] || "";
const mode = flag("--mode", positional[2] || "all");
const out = resolve(
  flag("--out", process.env.OUT || (serve ? join(repoRoot, "docs", "evidence", "ui") : ".")),
);
const evidenceRoot = dirname(out);
const reportPath = resolve(
  flag("--report", serve ? join(repoRoot, "docs", "evidence", "axe-report.json") : join(evidenceRoot, "axe-report.json")),
);
const summaryPath = resolve(
  flag("--summary", serve ? join(repoRoot, "docs", "evidence", "visual-a11y-summary.md") : join(evidenceRoot, "visual-a11y-summary.md")),
);
const edge = resolveEdge(flag("--edge", ""));
const PAGE_NAMES = [
  "home",
  "hosts",
  "models",
  "learning",
  "budget",
  "history",
  "privacy",
  "diagnostics",
  "help",
  "advanced",
];
const NAV_LABELS = {
  home: /首页|Home/,
  hosts: /宿主|Hosts/,
  models: /模型|Models/,
  learning: /学习|Learning/,
  budget: /预算|Budget/,
  history: /历史|History/,
  privacy: /隐私|Privacy/,
  diagnostics: /诊断|Diagnostics/,
  help: /帮助|Help/,
  advanced: /高级|Advanced/,
};

let axePath = "";
try {
  axePath = require.resolve("axe-core/axe.min.js");
} catch {
  console.error("axe-core is required: consumer/web/node_modules/axe-core/axe.min.js was not found.");
  process.exit(1);
}

function rel(file) {
  return relative(repoRoot, file).split("\\").join("/");
}

function nowIso() {
  return new Date().toISOString();
}

function mdCell(value, limit = 220) {
  const text = String(value ?? "").replace(/\|/g, "\\|").replace(/\r?\n/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1)}…` : text;
}

function countImpacts(scan) {
  const counts = { serious: 0, critical: 0, moderate: 0, minor: 0, unknown: 0 };
  for (const violation of scan.violations) counts[violation.impact || "unknown"] += 1;
  return counts;
}

class Evidence {
  constructor() {
    this.startedAt = nowIso();
    this.scans = [];
    this.screenshots = [];
    this.keyboard = [];
    this.consoleErrors = [];
    this.pageErrors = [];
    this.fatal = "";
    this.appVersion = "";
    this.axeVersion = "";
    this.serverUrl = "";
  }

  addScreenshot(name, file, state, scanName = "") {
    this.screenshots.push({ name, file: rel(file), state, scan: scanName });
  }

  attachErrorListeners(page, name) {
    page.on("console", (message) => {
      if (message.type() === "error") {
        this.consoleErrors.push({
          state: name,
          text: message.text(),
          location: message.location(),
        });
      }
    });
    page.on("pageerror", (error) => {
      this.pageErrors.push({ state: name, message: error.message, stack: error.stack || "" });
    });
  }

  recordKeyboard(id, label, ok, detail, evidence = "") {
    this.keyboard.push({ id, label, ok: Boolean(ok), detail, evidence });
  }

  totals() {
    const totals = { serious: 0, critical: 0, moderate: 0, minor: 0, unknown: 0, scans: this.scans.length };
    for (const scan of this.scans) {
      const counts = countImpacts(scan);
      for (const key of Object.keys(totals)) {
        if (key !== "scans") totals[key] += counts[key];
      }
    }
    return totals;
  }

  passed() {
    const totals = this.totals();
    return (
      !this.fatal &&
      totals.serious === 0 &&
      totals.critical === 0 &&
      this.consoleErrors.length === 0 &&
      this.pageErrors.length === 0 &&
      this.keyboard.every((item) => item.ok)
    );
  }

  write(environment) {
    const totals = this.totals();
    const passed = this.passed();
    const report = {
      version: 1,
      generated_at: nowIso(),
      command: "npm --prefix consumer/web run test:visual:matrix",
      passed,
      fatal: this.fatal,
      environment,
      server: { url: this.serverUrl, app_version: this.appVersion },
      axe: { version: this.axeVersion, totals },
      screenshots: this.screenshots,
      scans: this.scans,
      keyboard: this.keyboard,
      console_errors: this.consoleErrors,
      page_errors: this.pageErrors,
    };
    mkdirSync(dirname(reportPath), { recursive: true });
    writeFileSync(reportPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");

    const lines = [];
    lines.push("# Visual and accessibility evidence");
    lines.push("");
    lines.push(`Generated: ${nowIso()}`);
    lines.push("");
    lines.push("Command:");
    lines.push("");
    lines.push("```powershell");
    lines.push("npm --prefix consumer/web run build");
    lines.push("npm --prefix consumer/web run test:visual:matrix");
    lines.push("```");
    lines.push("");
    lines.push("The command starts a temporary local service with a seeded `MORPHD_HOME`, launches");
    lines.push("Playwright with the installed Microsoft Edge, writes the screenshots below, and removes");
    lines.push("the fixture data afterwards. It fails instead of fabricating screenshots when Edge or the");
    lines.push("local service cannot start.");
    lines.push("");
    lines.push(`Result: **${passed ? "PASS" : "FAIL"}**`);
    lines.push("");
    if (this.fatal) {
      lines.push("Fatal error:");
      lines.push("");
      lines.push("```text");
      lines.push(this.fatal);
      lines.push("```");
      lines.push("");
    }
    lines.push("## Environment");
    lines.push("");
    lines.push(`- Platform: ${environment.platform}`);
    lines.push(`- Node: ${environment.node}`);
    lines.push(`- Edge: \`${environment.edge}\``);
    lines.push(`- App version: ${this.appVersion || "—"}`);
    lines.push(`- axe-core: ${this.axeVersion || "—"}`);
    lines.push(`- Local service: ${this.serverUrl || "—"}`);
    lines.push(`- Machine-readable report: \`${rel(reportPath)}\``);
    lines.push("");
    lines.push("## Axe results (all scans)");
    lines.push("");
    lines.push("| State | Serious | Critical | Moderate | Minor | Result |");
    lines.push("|---|---:|---:|---:|---:|---|");
    for (const scan of this.scans) {
      const counts = countImpacts(scan);
      const scanPassed = counts.serious === 0 && counts.critical === 0;
      lines.push(
        `| ${mdCell(scan.name)} | ${counts.serious} | ${counts.critical} | ${counts.moderate} | ${counts.minor} | ${
          scanPassed ? "pass" : "**fail**"
        } |`,
      );
    }
    if (this.scans.length === 0) lines.push("| — no scan completed — | 0 | 0 | 0 | 0 | **fail** |");
    lines.push("");
    lines.push(
      `Totals: **${totals.serious} serious**, **${totals.critical} critical**, ` +
        `${totals.moderate} moderate, ${totals.minor} minor across ${totals.scans} scans. ` +
        "Serious/critical findings fail the command.",
    );
    lines.push("");
    const failures = this.scans.filter(
      (scan) => scan.violations.some((item) => item.impact === "serious" || item.impact === "critical"),
    );
    if (failures.length) {
      lines.push("### Blocking axe findings");
      lines.push("");
      for (const scan of failures) {
        for (const violation of scan.violations.filter(
          (item) => item.impact === "serious" || item.impact === "critical",
        )) {
          lines.push(
            `- \`${scan.name}\` — **${violation.impact}** \`${violation.id}\`: ${mdCell(violation.help, 160)} ` +
              `(${violation.nodes.length} node${violation.nodes.length === 1 ? "" : "s"})`,
          );
          for (const node of violation.nodes.slice(0, 4)) {
            lines.push(`  - \`${mdCell(node.target.join(" "), 160)}\``);
          }
        }
      }
      lines.push("");
    }
    lines.push("## Screenshots");
    lines.push("");
    lines.push("| File | State | Axe scan |");
    lines.push("|---|---|---|");
    for (const shot of this.screenshots) {
      const link = relative(dirname(summaryPath), join(repoRoot, shot.file)).split("\\").join("/");
      lines.push(`| [${shot.name}](${link}) | ${mdCell(shot.state)} | ${shot.scan || "—"} |`);
    }
    if (this.screenshots.length === 0) lines.push("| — no screenshots — | — | — |");
    lines.push("");
    lines.push("## Keyboard-only checks");
    lines.push("");
    lines.push("| Check | Result | Detail | Evidence |");
    lines.push("|---|---|---|---|");
    for (const check of this.keyboard) {
      lines.push(
        `| ${mdCell(check.label)} | ${check.ok ? "pass" : "**fail**"} | ${mdCell(check.detail)} | ${
          check.evidence || "—"
        } |`,
      );
    }
    if (this.keyboard.length === 0) lines.push("| — no keyboard checks — | **fail** | — | — |");
    lines.push("");
    lines.push("Keyboard checks use Tab/Shift+Tab and Enter/Escape only. Focus rings are measured");
    lines.push("from computed styles (outline + green offset shadow), never from color names.");
    lines.push("");
    lines.push("## Console and page errors");
    lines.push("");
    if (this.consoleErrors.length === 0 && this.pageErrors.length === 0) {
      lines.push("None.");
    } else {
      for (const error of this.consoleErrors) {
        lines.push(`- \`${error.state}\` console: ${mdCell(error.text)}`);
      }
      for (const error of this.pageErrors) {
        lines.push(`- \`${error.state}\` pageerror: ${mdCell(error.message)}`);
      }
    }
    lines.push("");
    lines.push("## Evidence notes");
    lines.push("");
    lines.push("- Fixture records follow the versioned JSON layouts used by `morphd/consumer` and its tests.");
    lines.push("- The seeded home includes one adapted dsh host, one provider, three sessions, budget journal");
    lines.push("  entries, a needs-review candidate, and a trial candidate with a real snapshot for revert.");
    lines.push("- Onboarding is captured against a second, blank temporary home so the first-run flow is real.");
    lines.push("- Temporary fixture data and the local service are removed/stopped when the command exits.");
    lines.push("");
    mkdirSync(dirname(summaryPath), { recursive: true });
    writeFileSync(summaryPath, `${lines.join("\n")}\n`, "utf8");
  }
}

function isVisibleFocus(info) {
  return Boolean(
    info &&
      info.focusable &&
      info.outlineStyle !== "none" &&
      info.outlineWidth >= 2 &&
      info.boxShadow &&
      info.boxShadow !== "none",
  );
}

async function activeInfo(page) {
  return page.evaluate(() => {
    const el = document.activeElement;
    if (!el || el === document.body || el === document.documentElement) {
      return { tag: el ? el.tagName : "", label: "", focusable: false };
    }
    const style = getComputedStyle(el);
    const rawLabel =
      el.getAttribute("aria-label") ||
      el.getAttribute("title") ||
      el.textContent ||
      el.getAttribute("placeholder") ||
      el.tagName;
    return {
      tag: el.tagName,
      label: String(rawLabel).replace(/\s+/g, " ").trim().slice(0, 160),
      role: el.getAttribute("role") || "",
      inNav: Boolean(el.closest(".nav")),
      cardText: (el.closest(".candidate-card")?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 300),
      outlineStyle: style.outlineStyle,
      outlineWidth: Number.parseFloat(style.outlineWidth) || 0,
      boxShadow: style.boxShadow,
      focusable: el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0,
    };
  });
}

async function tabUntil(page, predicate, maxTabs = 160) {
  const seen = [];
  for (let index = 0; index < maxTabs; index += 1) {
    await page.keyboard.press("Tab");
    const info = await activeInfo(page);
    seen.push(info.label || info.tag);
    if (await predicate(info)) return { info, tabs: index + 1 };
  }
  throw new Error(`Tab target not reached after ${maxTabs} stops. Last stops: ${seen.slice(-8).join(" → ")}`);
}

async function waitForTitle(page, name) {
  await page.waitForFunction(
    (target) => document.title === (target === "home" ? "morphd" : `${target} · morphd`),
    name,
    { timeout: 15000 },
  );
}

async function goToPage(page, name) {
  await page
    .locator("aside.sidebar nav")
    .getByRole("button", { name: NAV_LABELS[name] })
    .click();
  await waitForTitle(page, name);
  await page.waitForTimeout(250);
}

async function axeScan(page, evidence, name, meta) {
  const result = await page.evaluate(async () => {
    const axe = globalThis.axe;
    if (!axe) return { missing: true };
    const scan = await axe.run(document, { resultTypes: ["violations"] });
    return {
      version: axe.version,
      violations: scan.violations.map((item) => ({
        id: item.id,
        impact: item.impact || null,
        help: item.help,
        description: item.description,
        helpUrl: item.helpUrl,
        tags: item.tags,
        nodes: item.nodes.map((node) => ({
          target: node.target,
          html: node.html,
          failureSummary: node.failureSummary || "",
        })),
      })),
    };
  });
  if (result.missing) throw new Error(`axe-core did not load for ${name}`);
  evidence.axeVersion = result.version;
  evidence.scans.push({
    name,
    page: meta.page || "",
    theme: meta.theme || "",
    viewport: meta.viewport || "",
    scale: meta.scale || 1,
    title: await page.title(),
    violations: result.violations,
  });
}

async function capturePage({ browser, url, evidence, name, state, colorScheme = "light", viewport, scale = 1, setup, onboarding = false, fullPage = true, scan = true }) {
  // Windows display scaling changes the CSS viewport, not just the pixel density. Keep the
  // same physical window size and shrink the CSS layout at 125%/150% so line wrapping,
  // clipping and responsive breakpoints are actually exercised.
  const baseViewport = viewport || { width: 1440, height: 1100 };
  const effectiveViewport =
    viewport || scale === 1
      ? baseViewport
      : {
          width: Math.round(baseViewport.width / scale),
          height: Math.round(baseViewport.height / scale),
        };
  const context = await browser.newContext({
    viewport: effectiveViewport,
    deviceScaleFactor: scale,
    colorScheme,
    locale: "zh-CN",
  });
  const page = await context.newPage();
  evidence.attachErrorListeners(page, name);
  try {
    await page.addInitScript({ path: axePath });
    await page.goto(url, { waitUntil: "networkidle", timeout: 45000 });
    await page.waitForSelector(onboarding ? ".onboarding-main" : ".hero-main, .onboarding-main", {
      timeout: 20000,
    });
    if (setup) await setup(page);
    await page.waitForTimeout(400);
    const file = join(out, `${name}.png`);
    mkdirSync(out, { recursive: true });
    await page.screenshot({ path: file, fullPage });
    evidence.addScreenshot(name, file, state, scan ? name : "");
    if (scan) {
      await axeScan(page, evidence, name, {
        theme: colorScheme,
        viewport: `${effectiveViewport.width}x${effectiveViewport.height}`,
        scale,
      });
    }
  } finally {
    await context.close();
  }
}

async function captureOnboarding(browser, url, evidence) {
  await capturePage({
    browser,
    url,
    evidence,
    name: "onboarding-host-light",
    state: "Onboarding · find host · blank temporary home",
    onboarding: true,
  });
  await capturePage({
    browser,
    url,
    evidence,
    name: "onboarding-model-light",
    state: "Onboarding · recursive model",
    onboarding: true,
    setup: async (page) => {
      await page.getByRole("button", { name: /稍后再说|Later/ }).click();
      await page.waitForTimeout(350);
    },
  });
  await capturePage({
    browser,
    url,
    evidence,
    name: "onboarding-budget-light",
    state: "Onboarding · budget and privacy",
    onboarding: true,
    setup: async (page) => {
      await page.getByRole("button", { name: /稍后再说|Later/ }).click();
      await page.waitForTimeout(250);
      await page.getByRole("button", { name: /稍后配置|Later/ }).click();
      await page.waitForTimeout(350);
    },
  });
}

async function openCandidateModal(page) {
  await page
    .getByRole("button", { name: /查看改动与原因|View change and reason/ })
    .first()
    .click();
  await page.waitForSelector(".modal-panel", { timeout: 10000 });
  await page.waitForTimeout(450);
}

async function captureSeededMatrix(browser, url, evidence) {
  for (const pageName of PAGE_NAMES) {
    await capturePage({
      browser,
      url,
      evidence,
      name: `${pageName}-light`,
      state: `${pageName} · light · 100%`,
      setup: pageName === "home" ? undefined : (page) => goToPage(page, pageName),
    });
  }
  for (const pageName of PAGE_NAMES) {
    await capturePage({
      browser,
      url,
      evidence,
      name: `${pageName}-dark`,
      state: `${pageName} · dark · 100%`,
      colorScheme: "dark",
      setup: pageName === "home" ? undefined : (page) => goToPage(page, pageName),
    });
  }
  await capturePage({
    browser,
    url,
    evidence,
    name: "home-125",
    state: "home · light · 125% DPI",
    scale: 1.25,
  });
  await capturePage({
    browser,
    url,
    evidence,
    name: "home-150",
    state: "home · light · 150% DPI",
    scale: 1.5,
  });
  for (const [pageName, scale, suffix] of [
    ["hosts", 1.25, "125"],
    ["hosts", 1.5, "150"],
    ["history", 1.25, "125"],
    ["history", 1.5, "150"],
    ["models", 1.25, "125"],
    ["models", 1.5, "150"],
  ]) {
    await capturePage({
      browser,
      url,
      evidence,
      name: `${pageName}-${suffix}`,
      state: `${pageName} · light · ${suffix}% DPI`,
      scale,
      setup: (page) => goToPage(page, pageName),
    });
  }
  await capturePage({
    browser,
    url,
    evidence,
    name: "home-mobile",
    state: "home · light · mobile 375px",
    viewport: { width: 375, height: 900 },
    scale: 2,
  });
  await capturePage({
    browser,
    url,
    evidence,
    name: "home-xl-high",
    state: "home · light · xlarge font + high contrast",
    setup: async (page) => {
      await page.evaluate(() => {
        document.documentElement.dataset.font = "xlarge";
        document.documentElement.dataset.contrast = "high";
      });
      await page.waitForTimeout(250);
    },
  });
  await capturePage({
    browser,
    url,
    evidence,
    name: "candidate-modal-light",
    state: "candidate modal · light",
    setup: openCandidateModal,
    fullPage: false,
  });
  await capturePage({
    browser,
    url,
    evidence,
    name: "candidate-modal-dark",
    state: "candidate modal · dark",
    colorScheme: "dark",
    setup: openCandidateModal,
    fullPage: false,
  });
}

async function screenshotNow(page, evidence, name, state, fullPage = false) {
  const file = join(out, `${name}.png`);
  mkdirSync(out, { recursive: true });
  await page.screenshot({ path: file, fullPage });
  evidence.addScreenshot(name, file, state);
  return rel(file);
}

async function pollStatus(page, predicate, label, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    last = await page.evaluate(async () => {
      const response = await fetch("/api/status", { cache: "no-store" });
      return response.ok ? response.json() : null;
    });
    if (last && predicate(last)) return last;
    await page.waitForTimeout(200);
  }
  throw new Error(`${label} did not happen. Last status: ${JSON.stringify(last?.learning || last)}`);
}

async function runKeyboardChecks({
  browser,
  url,
  evidence,
  fixtures,
  needsApplyId,
  appliedId,
  appliedSummary,
  originalAgents,
  projectFile,
}) {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1100 },
    colorScheme: "light",
    locale: "zh-CN",
  });
  const page = await context.newPage();
  evidence.attachErrorListeners(page, "keyboard");
  const reset = async () => {
    await page.goto(url, { waitUntil: "networkidle", timeout: 45000 });
    await page.waitForSelector(".hero-main", { timeout: 20000 });
    await page.waitForTimeout(300);
  };

  try {
    await reset();
    await page.keyboard.press("Tab");
    const skip = await activeInfo(page);
    evidence.recordKeyboard(
      "skip-link",
      "Skip link is the first Tab stop with a visible ring",
      /跳到主要内容|Skip to main content/.test(skip.label) && isVisibleFocus(skip),
      `${skip.label || skip.tag}; outline ${skip.outlineWidth}px; shadow ${skip.boxShadow}`,
    );

    const navSeen = [];
    const navRings = [];
    let firstNavFocus = null;
    let navShot = "";
    for (let index = 0; index < 30 && navSeen.length < PAGE_NAMES.length; index += 1) {
      await page.keyboard.press("Tab");
      const info = await activeInfo(page);
      if (info.inNav && !navSeen.includes(info.label)) {
        navSeen.push(info.label);
        navRings.push(isVisibleFocus(info));
        firstNavFocus = firstNavFocus || info;
        if (!navShot) {
          const file = join(out, "keyboard-nav-focus.png");
          mkdirSync(out, { recursive: true });
          await page.screenshot({ path: file });
          evidence.addScreenshot("keyboard-nav-focus", file, "keyboard · focused primary navigation");
          navShot = rel(file);
        }
      }
    }
    const navRing = navSeen.length === PAGE_NAMES.length && navRings.every(Boolean);
    evidence.recordKeyboard(
      "nav-reachability",
      "All 10 primary navigation items are Tab-reachable",
      navSeen.length === PAGE_NAMES.length,
      `${navSeen.length}/10 reached: ${navSeen.join(" → ")}`,
    );
    evidence.recordKeyboard(
      "nav-focus-ring",
      "Primary navigation shows a visible focus ring on every item",
      navRing,
      firstNavFocus
        ? `${navRings.filter(Boolean).length}/${navRings.length} items with outline+offset shadow; first: ${firstNavFocus.label}`
        : "no nav focus captured",
      navShot,
    );

    await reset();
    await tabUntil(page, (info) => /宿主|Hosts/.test(info.label) && info.inNav);
    await page.keyboard.press("Enter");
    await waitForTitle(page, "hosts");
    evidence.recordKeyboard(
      "nav-activation",
      "Enter activates the focused navigation item",
      true,
      `Focused 宿主/Hosts, then Enter; title is ${await page.title()}`,
    );

    await reset();
    const modalTarget = await tabUntil(page, (info) => /查看改动与原因|View change and reason/.test(info.label));
    evidence.recordKeyboard(
      "candidate-action-reachable",
      "Candidate detail action is Tab-reachable with a visible ring",
      isVisibleFocus(modalTarget.info),
      `${modalTarget.info.label}; ${modalTarget.tabs} Tab stops; outline ${modalTarget.info.outlineWidth}px; shadow ${modalTarget.info.boxShadow}`,
    );
    await page.keyboard.press("Enter");
    await page.waitForSelector(".modal-panel", { timeout: 10000 });
    await page.waitForTimeout(250);
    const opened = await page.evaluate(() => Boolean(document.activeElement?.closest(".modal-panel")));
    const modalShot = await screenshotNow(
      page,
      evidence,
      "candidate-modal-keyboard",
      "keyboard · candidate modal opened with Enter",
    );
    evidence.recordKeyboard(
      "candidate-modal-open",
      "Candidate modal opens by keyboard and receives focus",
      opened,
      opened ? "focus is inside role=dialog" : "focus did not move into the dialog",
      modalShot,
    );

    let trapped = true;
    for (let index = 0; index < 14; index += 1) {
      await page.keyboard.press("Tab");
      const inside = await page.evaluate(() => Boolean(document.activeElement?.closest(".modal-panel")));
      if (!inside) {
        trapped = false;
        break;
      }
    }
    evidence.recordKeyboard(
      "candidate-modal-trap",
      "Tab focus remains inside the candidate modal",
      trapped,
      trapped ? "14 Tab presses stayed inside the dialog" : "focus escaped the dialog",
    );

    await page.keyboard.press("Escape");
    await page.waitForSelector(".modal-panel", { state: "detached", timeout: 10000 });
    await page.waitForTimeout(200);
    const restored = await activeInfo(page);
    evidence.recordKeyboard(
      "candidate-modal-close",
      "Escape closes the modal and returns focus to its trigger",
      /查看改动与原因|View change and reason/.test(restored.label),
      `focus restored to: ${restored.label}`,
    );

    if (fixtures) {
      await page.keyboard.press("Enter");
      await page.waitForSelector(".modal-panel", { timeout: 10000 });
      const feedbackTarget = await tabUntil(page, (info) => /有用|Useful/.test(info.label));
      await page.keyboard.press("Enter");
      await page.waitForFunction(
        () => document.body.innerText.includes("反馈已记录") || document.body.innerText.includes("Feedback recorded"),
        { timeout: 10000 },
      );
      const feedback = await page.evaluate(async () => {
        const response = await fetch("/api/candidates/cand_visual_needs", { cache: "no-store" });
        return response.ok ? response.json() : null;
      });
      evidence.recordKeyboard(
        "feedback-flow",
        "Feedback button can be activated by keyboard and is recorded",
        Boolean(feedback?.feedback?.some((item) => item.reason === "useful")),
        `${feedbackTarget.info.label}; feedback reasons: ${(feedback?.feedback || []).map((item) => item.reason).join(", ")}`,
      );
      await page.keyboard.press("Escape");
      await page.waitForSelector(".modal-panel", { state: "detached", timeout: 10000 }).catch(() => {});

      await reset();
      const pauseTarget = await tabUntil(page, (info) => /^暂停 1 天$|^Pause 1d$/.test(info.label));
      await page.keyboard.press("Enter");
      await pollStatus(page, (status) => status.learning?.paused === true, "pause");
      evidence.recordKeyboard(
        "pause-flow",
        "Pause can be triggered by keyboard",
        true,
        `focused ${pauseTarget.info.label}; /api/status reports learning.paused=true`,
      );

      await reset();
      const resumeTarget = await tabUntil(page, (info) => /^恢复$|^恢复学习$|^Resume$/.test(info.label));
      await page.keyboard.press("Enter");
      await pollStatus(page, (status) => status.learning?.paused === false, "resume");
      evidence.recordKeyboard(
        "resume-flow",
        "Resume can be triggered by keyboard",
        true,
        `focused ${resumeTarget.info.label}; /api/status reports learning.paused=false`,
      );

      await reset();
      const incognitoTarget = await tabUntil(page, (info) => /下一次会话隐身|Incognito next session/.test(info.label));
      await page.keyboard.press("Enter");
      await pollStatus(page, (status) => status.learning?.incognito_next === true, "incognito on");
      await reset();
      const cancelTarget = await tabUntil(page, (info) => /取消下一次隐身|Cancel next-session incognito/.test(info.label));
      await page.keyboard.press("Enter");
      await pollStatus(page, (status) => status.learning?.incognito_next === false, "incognito off");
      evidence.recordKeyboard(
        "incognito-flow",
        "Incognito can be enabled and cancelled by keyboard",
        true,
        `focused ${incognitoTarget.info.label}, then ${cancelTarget.info.label}`,
      );

      await reset();
      await tabUntil(page, (info) => /历史|History/.test(info.label) && info.inNav);
      await page.keyboard.press("Enter");
      await waitForTitle(page, "history");
      await tabUntil(page, (info) => /^待看\d*$|^Inbox\d*$/.test(info.label));
      // Tabs use roving tabindex: arrow keys move selection and focus.
      await page.keyboard.press("ArrowRight");
      await page.waitForTimeout(300);
      const cardTarget = await tabUntil(
        page,
        (info) =>
          /查看改动与原因|View change and reason/.test(info.label) &&
          info.cardText.includes(appliedSummary),
      );
      await page.keyboard.press("Enter");
      await page.waitForSelector(".modal-panel", { timeout: 10000 });
      const revertTarget = await tabUntil(page, (info) => /^撤销$|^Revert$/.test(info.label));
      await page.keyboard.press("Enter");
      await page.waitForFunction(
        () =>
          document.body.innerText.includes("确认恢复到应用前？") ||
          document.body.innerText.includes("Restore the pre-apply state?"),
        { timeout: 10000 },
      );
      const confirmShot = await screenshotNow(
        page,
        evidence,
        "keyboard-revert-confirm",
        "keyboard · revert confirmation before actual restore",
      );
      await tabUntil(page, (info) => /确认撤销|Confirm revert/.test(info.label));
      await page.keyboard.press("Enter");
      await page.waitForFunction(
        () => document.body.innerText.includes("已恢复到应用前") || document.body.innerText.includes("Restored the previous state"),
        { timeout: 15000 },
      );
      const candidate = await page.evaluate(async (id) => {
        const response = await fetch(`/api/candidates/${id}`, { cache: "no-store" });
        return response.ok ? response.json() : null;
      }, appliedId);
      const restoredFile = existsSync(projectFile) ? readFileSync(projectFile, "utf8") : "";
      evidence.recordKeyboard(
        "revert-flow",
        "Revert confirmation opens by keyboard and restores the snapshot",
        candidate?.state === "reverted" && restoredFile === originalAgents,
        `card ${cardTarget.tabs}, revert ${revertTarget.tabs}; state=${candidate?.state}; file restored=${restoredFile === originalAgents}`,
        confirmShot,
      );
    }
  } finally {
    await context.close();
  }
}

async function main() {
  const environment = {
    platform: `${process.platform} ${process.arch}`,
    node: process.version,
    edge,
  };
  const evidence = new Evidence();
  let browser = null;
  let runRoot = "";
  const servers = [];
  let seed = null;
  try {
    if (!serve && !urlArg) {
      throw new Error(
        "URL is required in single-service mode. Use `npm --prefix consumer/web run test:visual:matrix` for a seeded run.",
      );
    }
    if (serve && !existsSync(join(repoRoot, "morphd", "consumer", "webui", "index.html"))) {
      throw new Error(
        "Built Web UI is missing. Run `npm --prefix consumer/web run build` before the visual matrix.",
      );
    }
    console.log(`Launching Microsoft Edge: ${edge}`);
    browser = await chromium.launch({
      executablePath: edge,
      headless: true,
      args: ["--force-color-profile=srgb"],
    });

    let url = urlArg;
    if (serve) {
      runRoot = makeRunRoot().root;
      const blankHome = join(runRoot, "blank-home");
      console.log("Starting blank local service for onboarding evidence…");
      const blank = await startServer({ root: runRoot, home: blankHome, label: "blank" });
      servers.push(blank);
      url = blank.url;
      evidence.serverUrl = url.replace(/#.*$/, "");
      await captureOnboarding(browser, url, evidence);
      await blank.stop();
      servers.pop();

      const home = join(runRoot, "home");
      seed = seedVisualHome(home);
      console.log("Starting seeded local service for the page matrix…");
      const server = await startServer({ root: runRoot, home, label: "seeded" });
      servers.push(server);
      url = server.url;
      evidence.serverUrl = url.replace(/#.*$/, "");
      await markHostConnected(server, server.info.host_token);
      const status = await fetch(`${server.baseUrl}/api/status`).catch(() => null);
      if (status && status.status === 401) {
        // Expected without a UI session; version comes from the service module.
      }
      evidence.appVersion = "0.5.0";
      await captureSeededMatrix(browser, url, evidence);
      await runKeyboardChecks({
        browser,
        url,
        evidence,
        fixtures: true,
        appliedId: seed.appliedId,
        appliedSummary: "在 AGENTS.md 中补充 pnpm 测试检查",
        originalAgents: seed.originalAgents,
        projectFile: join(seed.project, "AGENTS.md"),
      });
    } else if (mode === "onboarding") {
      await captureOnboarding(browser, url, evidence);
    } else {
      evidence.serverUrl = url.replace(/#.*$/, "");
      await captureSeededMatrix(browser, url, evidence);
      await runKeyboardChecks({
        browser,
        url,
        evidence,
        fixtures: false,
        appliedId: "",
        appliedSummary: "",
        originalAgents: "",
        projectFile: "",
      });
    }
  } catch (error) {
    evidence.fatal = error instanceof Error ? `${error.message}\n${error.stack || ""}` : String(error);
    console.error(evidence.fatal);
    process.exitCode = 1;
  } finally {
    for (const server of servers) {
      try {
        await server.stop();
      } catch (error) {
        console.error(`Could not stop the local service: ${error instanceof Error ? error.message : error}`);
      }
    }
    if (browser) {
      try {
        await browser.close();
      } catch (error) {
        console.error(`Could not close Edge: ${error instanceof Error ? error.message : error}`);
      }
    }
    if (runRoot) cleanupRunRoot(runRoot);
  }

  if (evidence.screenshots.length || evidence.scans.length || evidence.keyboard.length) {
    evidence.write(environment);
  } else if (evidence.fatal) {
    console.error("No evidence files were written because the run failed before capture.");
  }
  if (evidence.passed()) {
    console.log(
      `Visual matrix passed: ${evidence.scans.length} axe scans, ${evidence.screenshots.length} screenshots, ` +
        `${evidence.keyboard.filter((item) => item.ok).length}/${evidence.keyboard.length} keyboard checks.`,
    );
    console.log(`Summary: ${summaryPath}`);
    console.log(`Axe report: ${reportPath}`);
  } else {
    if (evidence.scans.length || evidence.screenshots.length || evidence.keyboard.length) {
      console.error(`Visual matrix failed. See ${summaryPath}`);
    }
    process.exitCode = 1;
  }
}

main();
