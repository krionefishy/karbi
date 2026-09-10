import { afterEach, describe, expect, it, vi } from "vitest";

import { setComment, setMark } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("checklist api", () => {
  it("puts a tick on one item of one card", async () => {
    const request = vi.fn(async (_url: string, _init?: RequestInit) => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", request);

    await setMark("seller-1", "1304367626", "rich_content", true);

    const [url, init] = request.mock.calls[0];
    expect(url).toBe("/api/v1/wb/card-checklist/sellers/seller-1/articles/1304367626/items/rich_content");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ checked: true });
  });

  it("escapes the article in the comment path", async () => {
    const request = vi.fn(async (_url: string, _init?: RequestInit) => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", request);

    await setComment("seller-1", "A/B", "Ждём видео");

    expect(request.mock.calls[0][0]).toBe("/api/v1/wb/card-checklist/sellers/seller-1/articles/A%2FB/comment");
  });
});
