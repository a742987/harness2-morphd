export interface SettingDiff {
  path: string;
  current: unknown;
  incoming: unknown;
}

export const KNOWN_SETTINGS_KEYS = new Set([
  "version",
  "enabled",
  "trust_level",
  "change_appetite",
  "auto_apply",
  "auto_apply_window",
  "auto_rollback",
  "trial_sessions",
  "undo_days",
  "autopilot_ack",
  "budget",
  "cadence",
  "privacy",
  "ui",
  "models",
  "analysis",
  "enterprise",
]);

const UNSAFE_KEYS = new Set(["__proto__", "prototype", "constructor"]);
export const MAX_IMPORT_ROWS = 200;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function flattenSettings(
  value: Record<string, unknown>,
  prefix = "",
): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    if (UNSAFE_KEYS.has(key)) continue;
    const path = prefix ? `${prefix}.${key}` : key;
    if (isPlainObject(item)) {
      Object.assign(result, flattenSettings(item, path));
    } else {
      result[path] = item;
    }
  }
  return result;
}

export function extractSettingsPayload(payload: unknown): Record<string, unknown> | null {
  if (!isPlainObject(payload)) return null;
  const nested = payload.settings;
  if (isPlainObject(nested)) {
    return Object.fromEntries(
      Object.entries(nested).filter(([key]) => KNOWN_SETTINGS_KEYS.has(key)),
    );
  }
  if (Object.keys(payload).some((key) => KNOWN_SETTINGS_KEYS.has(key))) {
    return Object.fromEntries(
      Object.entries(payload).filter(([key]) => KNOWN_SETTINGS_KEYS.has(key)),
    );
  }
  return null;
}

export function diffSettings(
  current: Record<string, unknown>,
  incoming: Record<string, unknown>,
): SettingDiff[] {
  const flatCurrent = flattenSettings(current);
  const flatIncoming = flattenSettings(
    Object.fromEntries(
      Object.entries(incoming).filter(([key]) => KNOWN_SETTINGS_KEYS.has(key)),
    ),
  );
  const rows: SettingDiff[] = [];
  for (const path of Object.keys(flatIncoming).sort()) {
    if (path === "version") continue;
    const next = flatIncoming[path];
    const previous = flatCurrent[path];
    if (JSON.stringify(previous) === JSON.stringify(next)) continue;
    rows.push({ path, current: previous, incoming: next });
  }
  return rows;
}

export function buildSettingsPatch(
  rows: SettingDiff[],
  selected: Record<string, boolean>,
): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  for (const row of rows) {
    if (!selected[row.path]) continue;
    const parts = row.path.split(".");
    if (parts.some((part) => UNSAFE_KEYS.has(part))) continue;
    let target = patch;
    for (const part of parts.slice(0, -1)) {
      const next = target[part];
      if (!isPlainObject(next)) target[part] = {};
      target = target[part] as Record<string, unknown>;
    }
    target[parts[parts.length - 1]] = row.incoming;
  }
  return patch;
}

export function describeValue(value: unknown): string {
  if (value === undefined) return "—";
  if (typeof value === "string") return value || '""';
  return JSON.stringify(value);
}
