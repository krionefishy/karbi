import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getQueue,
  getSettings,
  getSubmissions,
  getSummary,
  resolveSubmission,
  setOutcome,
  updateSettings,
  uploadSubmission,
} from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

describe("screenshot control api", () => {
  it("uploads a screenshot as multipart with the client id and outcome", async () => {
    const request = vi.fn(async (_url: string, _init?: RequestInit) => json({ id: "s-1" }, 201));
    vi.stubGlobal("fetch", request);

    const file = new File([new Uint8Array([137, 80, 78, 71])], "shot.png", { type: "image/png" });
    await uploadSubmission({ file, clientSubmissionId: "c-1", outcome: "amended" });

    const [url, init] = request.mock.calls[0];
    expect(url).toBe("/api/v1/wb/screenshot-control/submissions");
    expect(init?.method).toBe("POST");
    const body = init?.body as FormData;
    expect(body.get("client_submission_id")).toBe("c-1");
    expect(body.get("outcome")).toBe("amended");
    expect(body.get("file")).toBeInstanceOf(File);
  });

  it("drops empty filters from the query", async () => {
    const request = vi.fn(async (_url: string, _init?: RequestInit) => json({ items: [], next_cursor: null }));
    vi.stubGlobal("fetch", request);

    await getSubmissions({ status: "needs_review", outcome: undefined, from: "2026-10-01" });
    await getSummary();
    await getQueue({ kind: "duplicates", limit: 20 });

    expect(request.mock.calls[0][0]).toBe("/api/v1/wb/screenshot-control/submissions?status=needs_review&from=2026-10-01");
    expect(request.mock.calls[1][0]).toBe("/api/v1/wb/screenshot-control/summary");
    expect(request.mock.calls[2][0]).toBe("/api/v1/admin/screenshot-control/queue?kind=duplicates&limit=20");
  });

  it("sends json bodies for outcome changes and admin decisions", async () => {
    const request = vi.fn(async (_url: string, _init?: RequestInit) => json({}));
    vi.stubGlobal("fetch", request);

    await setOutcome("s-1", "deleted");
    await resolveSubmission("s-1", { decision: "decline", note: "чужой кейс" });

    expect(request.mock.calls[0][1]?.method).toBe("PATCH");
    expect(JSON.parse(String(request.mock.calls[0][1]?.body))).toEqual({ outcome: "deleted" });
    expect(request.mock.calls[1][0]).toBe("/api/v1/admin/screenshot-control/submissions/s-1/resolve");
    expect(JSON.parse(String(request.mock.calls[1][1]?.body))).toEqual({ decision: "decline", note: "чужой кейс" });
  });

  it("keeps the machine error code from X-Error-Code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "Нужен комментарий" }), {
          status: 422,
          headers: { "Content-Type": "application/json", "X-Error-Code": "note_required" },
        }),
      ),
    );

    await expect(resolveSubmission("s-1", { decision: "decline", note: "" })).rejects.toMatchObject({
      status: 422,
      code: "note_required",
      message: "Нужен комментарий",
    });
  });
  it("reads and saves the manager timezone", async () => {
    const answer = { timezone: "Asia/Vladivostok", zones: [], default: "Europe/Moscow" };
    const request = vi.fn(async (_url: string, _init?: RequestInit) => json(answer));
    vi.stubGlobal("fetch", request);

    await getSettings();
    expect(await updateSettings("Asia/Vladivostok")).toEqual(answer);

    expect(request.mock.calls[0][0]).toBe("/api/v1/wb/screenshot-control/settings");
    expect(request.mock.calls[0][1]?.method).toBeUndefined();
    const [url, init] = request.mock.calls[1];
    expect(url).toBe("/api/v1/wb/screenshot-control/settings");
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ timezone: "Asia/Vladivostok" });
  });
});
