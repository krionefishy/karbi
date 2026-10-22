import { afterEach, describe, expect, it, vi } from "vitest";

import { getCardDocuments, getInviteLink, getRefreshState, requestRefresh } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("card documents api", () => {
  it("reads the seller page and passes the state filter", async () => {
    const request = vi.fn(async (_url: string, _init?: RequestInit) => json({ items: [] }));
    vi.stubGlobal("fetch", request);

    await getCardDocuments("seller-1");
    await getCardDocuments("seller-1", "rejected");

    expect(request.mock.calls[0][0]).toBe("/api/v1/wb/card-documents/sellers/seller-1");
    expect(request.mock.calls[1][0]).toBe("/api/v1/wb/card-documents/sellers/seller-1?state=rejected");
  });

  it("asks for a refresh and reads its state", async () => {
    const request = vi.fn(async (_url: string, init?: RequestInit) =>
      init?.method === "POST" ? json({ status: "queued", in_progress: true }, 202) : json(null),
    );
    vi.stubGlobal("fetch", request);

    await expect(requestRefresh("seller-1")).resolves.toMatchObject({ status: "queued" });
    await expect(getRefreshState("seller-1")).resolves.toBeNull();

    expect(request.mock.calls[0][0]).toBe("/api/v1/wb/card-documents/sellers/seller-1/refresh");
    expect(request.mock.calls[0][1]?.method).toBe("POST");
    expect(request.mock.calls[1][1]?.method).toBeUndefined();
  });

  it("issues a bot invite link", async () => {
    const request = vi.fn(async (_url: string, _init?: RequestInit) =>
      json({ url: "https://t.me/bot?start=abc", expires_at: "2026-10-23T07:00:00Z" }),
    );
    vi.stubGlobal("fetch", request);

    await expect(getInviteLink("seller-1")).resolves.toEqual({
      url: "https://t.me/bot?start=abc",
      expires_at: "2026-10-23T07:00:00Z",
    });
    expect(request.mock.calls[0][0]).toBe("/api/v1/wb/card-documents/sellers/seller-1/invite-link");
    expect(request.mock.calls[0][1]?.method).toBe("POST");
  });
});
