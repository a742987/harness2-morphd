import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

function jsonResponse(payload: unknown) {
  return {
    ok: true,
    status: 200,
    text: async () => JSON.stringify(payload),
  } as unknown as Response;
}

afterEach(() => {
  delete (window as { morphd?: unknown }).morphd;
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("folder picker", () => {
  it("prefers the Electron native dialog when the bridge is available", async () => {
    const pickFolder = vi.fn(async () => ({ path: "C:\\projects\\demo" }));
    window.morphd = { pickFolder };
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.pickFolder("project")).resolves.toEqual({ path: "C:\\projects\\demo" });
    expect(pickFolder).toHaveBeenCalledWith("project");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("treats a cancelled Electron dialog as an empty path", async () => {
    window.morphd = { pickFolder: async () => ({ path: "" }) };

    await expect(api.pickFolder("config")).resolves.toEqual({ path: "" });
  });

  it("falls back to the local API when no desktop bridge exists", async () => {
    const fetchMock = vi.fn(async (_path: string, _init?: RequestInit) =>
      jsonResponse({ path: "D:\\hosts\\dsh" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.pickFolder("install")).resolves.toEqual({ path: "D:\\hosts\\dsh" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/hosts/pick-folder");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ kind: "install" }));
  });
});

describe("manual projects", () => {
  it("encodes scope ids and uses the documented endpoints", async () => {
    const fetchMock = vi.fn(async (_path: string, _init?: RequestInit) =>
      jsonResponse({ ok: true, session: { id: "sess" } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.deleteManualProject("manual_a/b");
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/manual-projects/manual_a%2Fb",
      expect.objectContaining({ method: "DELETE" }),
    );

    await api.importManualProject("manual_a/b");
    expect(fetchMock).toHaveBeenLastCalledWith(
      "/api/manual-projects/manual_a%2Fb/import",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
