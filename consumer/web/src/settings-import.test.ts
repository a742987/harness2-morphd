import { describe, expect, it } from "vitest";
import {
  buildSettingsPatch,
  diffSettings,
  extractSettingsPayload,
  flattenSettings,
} from "./settings-import";

const current = {
  version: 1,
  trust_level: "advisor",
  budget: { monthly_usd: 5, per_job_usd: 1 },
  cadence: { weekly_cap: 2, quiet_hours: ["22:00", "08:00"] },
  privacy: { excluded_globs: ["node_modules"] },
  ui: { beginner: true },
};

describe("settings import", () => {
  it("flattens nested objects but keeps arrays as values", () => {
    expect(flattenSettings(current)).toMatchObject({
      trust_level: "advisor",
      "budget.monthly_usd": 5,
      "cadence.quiet_hours": ["22:00", "08:00"],
      "privacy.excluded_globs": ["node_modules"],
    });
  });

  it("diffs only changed known leaves and ignores version", () => {
    const incoming = {
      version: 99,
      trust_level: "assistant",
      budget: { monthly_usd: 5, per_job_usd: 2 },
      cadence: { weekly_cap: 2, quiet_hours: ["21:00", "07:00"] },
      privacy: { excluded_globs: ["node_modules", "secrets/**"] },
      ui: { beginner: true },
      unknown_section: { dangerous: true },
    };
    expect(diffSettings(current, incoming)).toEqual([
      { path: "budget.per_job_usd", current: 1, incoming: 2 },
      { path: "cadence.quiet_hours", current: ["22:00", "08:00"], incoming: ["21:00", "07:00"] },
      {
        path: "privacy.excluded_globs",
        current: ["node_modules"],
        incoming: ["node_modules", "secrets/**"],
      },
      { path: "trust_level", current: "advisor", incoming: "assistant" },
    ]);
  });

  it("builds a nested patch from selected rows only", () => {
    const rows = diffSettings(current, {
      trust_level: "assistant",
      budget: { monthly_usd: 8, per_job_usd: 2 },
      cadence: { weekly_cap: 3 },
    });
    expect(buildSettingsPatch(rows, { "trust_level": true, "budget.monthly_usd": true })).toEqual({
      trust_level: "assistant",
      budget: { monthly_usd: 8 },
    });
  });

  it("extracts wrapped or raw settings and rejects unrelated JSON", () => {
    expect(extractSettingsPayload({ settings: current })).toEqual(current);
    expect(extractSettingsPayload(current)).toEqual(current);
    expect(extractSettingsPayload({ sessions: [] })).toBeNull();
    expect(extractSettingsPayload([current])).toBeNull();
    expect(extractSettingsPayload("text")).toBeNull();
  });

  it("never writes prototype-polluting keys", () => {
    const malicious = JSON.parse(
      '{"__proto__":{"polluted":true},"prototype":{"polluted":true},"budget":{"__proto__":{"polluted":true},"monthly_usd":9}}',
    );
    const rows = diffSettings(current, malicious);
    expect(rows.some((row) => row.path.includes("__proto__") || row.path.includes("prototype"))).toBe(
      false,
    );
    const patch = buildSettingsPatch(
      [{ path: "__proto__.polluted", current: undefined, incoming: true }],
      { "__proto__.polluted": true },
    );
    expect(patch).toEqual({});
    expect(({} as { polluted?: boolean }).polluted).toBeUndefined();
  });
});
