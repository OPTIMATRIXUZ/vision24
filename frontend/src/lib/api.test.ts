import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function ok(body: unknown = {}) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("request defaults", () => {
  it("sends the session cookie on every request", async () => {
    const { api } = await import("./api");
    fetchMock.mockResolvedValue(ok());

    await api("/api/site");

    expect(fetchMock.mock.calls[0][1]).toMatchObject({ credentials: "include" });
  });

  it("carries no Authorization header", async () => {
    const { api } = await import("./api");
    fetchMock.mockResolvedValue(ok());

    await api("/api/site");

    const headers = (fetchMock.mock.calls[0][1]?.headers ?? {}) as Record<string, string>;
    expect(Object.keys(headers).map((k) => k.toLowerCase())).not.toContain("authorization");
  });
});

describe("site selection", () => {
  it("appends the chosen site to data requests", async () => {
    const { api, setSelectedSite } = await import("./api");
    fetchMock.mockResolvedValue(ok());
    setSelectedSite("site-b");

    await api("/api/cameras");

    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/cameras?site_id=site-b");
  });

  it("merges with an existing query string", async () => {
    const { api, setSelectedSite } = await import("./api");
    fetchMock.mockResolvedValue(ok());
    setSelectedSite("site-b");

    await api("/api/metrics/traffic?day=2026-07-30");

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      "/api/metrics/traffic?day=2026-07-30&site_id=site-b",
    );
  });

  it("sends nothing when the default site is selected", async () => {
    const { api, setSelectedSite } = await import("./api");
    fetchMock.mockResolvedValue(ok());
    setSelectedSite("");

    await api("/api/cameras");

    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/cameras");
  });

  it("leaves auth routes alone", async () => {
    const { api, setSelectedSite } = await import("./api");
    fetchMock.mockResolvedValue(ok());
    setSelectedSite("site-b");

    await api("/api/auth/me");

    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/auth/me");
  });

  it("does not override a site the caller asked for explicitly", async () => {
    const { api, setSelectedSite } = await import("./api");
    fetchMock.mockResolvedValue(ok());
    setSelectedSite("site-b");

    await api("/api/site?site_id=site-c");

    expect(String(fetchMock.mock.calls[0][0])).toBe("/api/site?site_id=site-c");
  });
});
