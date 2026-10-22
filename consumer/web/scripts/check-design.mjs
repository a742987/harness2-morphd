#!/usr/bin/env node
// Enforce the NVIDIA Green / Mono design contract (PRD §8.8 / FR-C18).
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, extname } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../src", import.meta.url));
const allowedHex = new Set(["#76B900", "#FFFFFF", "#000000"]);
const errors = [];

function files(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...files(path));
    else if ([".css", ".tsx", ".ts"].includes(extname(path))) out.push(path);
  }
  return out;
}

for (const path of files(root)) {
  const text = readFileSync(path, "utf8");
  const rel = path.replace(root, "src");
  for (const match of text.matchAll(/#[0-9a-fA-F]{3,8}\b/g)) {
    if (!allowedHex.has(match[0].toUpperCase())) errors.push(`${rel}: forbidden color ${match[0]}`);
  }
  if (/rgba?\(/i.test(text)) errors.push(`${rel}: rgba() is forbidden`);
  if (/hsla?\(/i.test(text)) errors.push(`${rel}: hsla() is forbidden`);
  if (/gradient\(/i.test(text)) errors.push(`${rel}: gradients are forbidden`);
  if (/\bopacity\s*:/i.test(text)) errors.push(`${rel}: opacity is forbidden in UI colors`);
  if (/border-radius\s*:\s*0(px|;|\s)/i.test(text)) errors.push(`${rel}: zero radius is forbidden`);
  if (/border-radius\s*:\s*(1[1-9]|[2-9]\d)px/i.test(text)) errors.push(`${rel}: radius >10px is forbidden`);
  if (/border-radius\s*:\s*50%/i.test(text)) errors.push(`${rel}: 50% radius is forbidden`);
  for (const match of text.matchAll(/box-shadow\s*:\s*([^;]+);/gi)) {
    const value = match[1];
    const parts = value.match(/-?\d+(\.\d+)?px/g) || [];
    if (parts.length >= 3) {
      const blur = parseFloat(parts[2]);
      if (blur !== 0) errors.push(`${rel}: blurred box-shadow is forbidden (${value.trim()})`);
    }
  }
}

if (errors.length) {
  console.error("design check failed:\n" + errors.map((e) => " - " + e).join("\n"));
  process.exit(1);
}
console.log("design check passed");
