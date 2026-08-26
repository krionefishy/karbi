import { afterEach, describe, expect, it, vi } from "vitest";

import { getRefreshState, requestRefresh } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("turnover refresh", () => {
  it("asks the server to queue a collection", async () => {
    const request = vi.fn(
      async (_url: string) =>
        new Response(JSON.stringify({ status: "queued", in_progress: true, requested_at: "x", finished_at: null, error: null }), {
          status: 202,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", request);

    const state = await requestRefresh("seller-1");

    expect(request).toHaveBeenCalledWith(
      "/api/v1/wb/turnover/sellers/seller-1/refresh",
      expect.objectContaining({ method: "POST" }),
    );
    expect(state.in_progress).toBe(true);
  });

  it("reads the state without asking for another collection", async () => {
    const request = vi.fn(
      async (_url: string, _init?: RequestInit) =>
        new Response("null", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", request);

    await getRefreshState("seller-1");

    const [, init] = request.mock.calls[0];
    expect(init?.method).toBeUndefined();
  });
});
