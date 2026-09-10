import { afterEach, describe, expect, it, vi } from "vitest";

import { setComment } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("checklist api", () => {
  it("saves a comment and escapes the article in the path", async () => {
    const request = vi.fn(async (_url: string, _init?: RequestInit) => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", request);

    await setComment("seller-1", "A/B", "Ждём видео");

    const [url, init] = request.mock.calls[0];
    expect(url).toBe("/api/v1/wb/card-checklist/sellers/seller-1/articles/A%2FB/comment");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ text: "Ждём видео" });
  });
});
