import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest, refreshAccessToken, setAccessToken } from "./http";

/** Заголовок Authorization, который клиент подставил бы следующему запросу. */
async function tokenInUse(): Promise<string | null> {
  const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  await apiRequest("/api/v1/probe");
  const headers = new Headers((fetchMock.mock.calls[0][1] as RequestInit).headers);
  return headers.get("Authorization");
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

describe("обновление сессии", () => {
  it("не роняет сессию, когда сервер занят", async () => {
    // 429 от лимитера — это «сейчас не получилось», а не «сессии больше нет»:
    // разлогинивать из-за него значит выбрасывать человека из интерфейса
    // каждый раз, когда открыто несколько вкладок с опросом.
    setAccessToken("live-token");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 429 })));

    await expect(refreshAccessToken()).rejects.toBeInstanceOf(ApiError);

    expect(await tokenInUse()).toBe("Bearer live-token");
  });

  it("роняет сессию, когда доступа действительно нет", async () => {
    setAccessToken("stale-token");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "no" }), { status: 401 })),
    );

    await expect(apiRequest("/api/v1/wb/sellers")).rejects.toBeInstanceOf(ApiError);

    expect(await tokenInUse()).toBeNull();
  });

  it("сохраняет сессию, если обновление не прошло из-за лимитера", async () => {
    setAccessToken("live-token");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "no" }), { status: 401 }))
      .mockResolvedValueOnce(new Response("{}", { status: 429 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiRequest("/api/v1/wb/sellers")).rejects.toBeInstanceOf(ApiError);

    expect(await tokenInUse()).toBe("Bearer live-token");
  });
});
