import { afterEach, describe, expect, it, vi } from "vitest";

import {
  archiveSeller,
  attachSeller,
  detachSeller,
  getSellers,
  purgeSeller,
  setOzonCredentials,
  verifyOzonEgress,
} from "./api";

function stubFetch() {
  const request = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", request);
  return request;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("seller registry api", () => {
  it("leaves an automation without touching the registry", async () => {
    const request = stubFetch();

    await detachSeller("wb-reviews", "seller-1");

    expect(request).toHaveBeenCalledWith(
      "/api/v1/automations/wb-reviews/sellers/seller-1",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("separates archiving from erasing", async () => {
    const request = stubFetch();

    await archiveSeller("seller-1");
    await purgeSeller("seller-1");

    expect(request.mock.calls[0][0]).toBe("/api/v1/wb/sellers/seller-1");
    expect(request.mock.calls[1][0]).toBe("/api/v1/wb/sellers/seller-1?purge=true");
  });

  it("asks for archived sellers only when they are wanted", async () => {
    const request = vi.fn(
      async (_url: string) =>
        new Response("[]", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", request);

    await getSellers();
    await getSellers(true);

    expect(request.mock.calls[0][0]).toBe("/api/v1/wb/sellers");
    expect(request.mock.calls[1][0]).toBe("/api/v1/wb/sellers?include_archived=true");
  });

  it("connects an existing seller by id", async () => {
    const request = vi.fn().mockResolvedValue(
      new Response("{}", { status: 201, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", request);

    await attachSeller("wb-reviews", { seller_id: "seller-1" });

    expect(request).toHaveBeenCalledWith(
      "/api/v1/automations/wb-reviews/sellers",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ seller_id: "seller-1" }) }),
    );
  });

  it("sends the whole Ozon account, since the gateway replaces it as a whole", async () => {
    const request = vi.fn().mockResolvedValue(
      new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", request);

    await setOzonCredentials("seller-1", {
      client_id: "111222",
      api_key: "ozon-api-key",
      performance_client_id: "42@advertising.performance.ozon.ru",
      performance_client_secret: "perf-secret",
    });

    expect(request).toHaveBeenCalledWith(
      "/api/v1/wb/sellers/seller-1/ozon",
      expect.objectContaining({ method: "PUT" }),
    );
    expect(JSON.parse(request.mock.calls[0][1].body)).toEqual({
      client_id: "111222",
      api_key: "ozon-api-key",
      performance_client_id: "42@advertising.performance.ozon.ru",
      performance_client_secret: "perf-secret",
    });
  });

  it("keeps the two marketplaces' re-checks apart", async () => {
    const request = vi.fn().mockResolvedValue(
      new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    vi.stubGlobal("fetch", request);

    await verifyOzonEgress("seller-1");

    expect(request).toHaveBeenCalledWith(
      "/api/v1/wb/sellers/seller-1/ozon-verify",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
