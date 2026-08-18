import { afterEach, describe, expect, it, vi } from "vitest";

import { getPlatformReadiness } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("platform readiness", () => {
  it("reads the live readiness endpoint without authentication refresh", async () => {
    const request = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ database: true, redis: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", request);

    await expect(getPlatformReadiness()).resolves.toEqual({ database: true, redis: true });
    expect(request).toHaveBeenCalledWith(
      "/api/v1/health/ready",
      expect.objectContaining({ credentials: "include" }),
    );
  });
});
