import { describe, expect, it } from "vitest";
import { formatMoney, humanError, resolveLang, timeAgo } from "./i18n";

describe("consumer formatting", () => {
  it("resolves system language", () => {
    expect(resolveLang("system", "zh-CN")).toBe("zh");
    expect(resolveLang("system", "en-US")).toBe("en");
    expect(resolveLang("en", "zh-CN")).toBe("en");
  });

  it("formats small USD amounts without turning them into zero", () => {
    expect(formatMoney(0.0001, "en")).toMatch(/0\.0001/);
    expect(formatMoney(null, "en")).toBe("unknown");
    expect(formatMoney(null, "zh")).toBe("未知");
  });

  it("never blames the user in budget errors and always answers cost", () => {
    const copy = humanError("BUDGET_EXCEEDED", "预算", "", "zh");
    expect(copy.what).toContain("预算");
    expect(copy.money).toContain("不会产生费用");
    expect(copy.action.length).toBeGreaterThan(0);
  });

  it("handles invalid timestamps", () => {
    expect(timeAgo("not-a-date", "en")).toBe("—");
    expect(timeAgo(null, "zh")).toBe("—");
  });
});
