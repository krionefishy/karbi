import { describe, expect, it, vi } from "vitest";
import {
  API_PREFIX,
  ApiError,
  call,
  classifyStatus,
  errorFromResponse,
  isFinal,
  moscowToday,
  normalizePairingCode,
  pair,
  parseRetryAfter,
  statusText,
  submissionForm,
  submit,
  type SubmissionOut,
} from "../src/api";

function errorResponse(status: number, detail: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify({ detail }), { status, headers: { "Content-Type": "application/json", ...headers } });
}

const submission: SubmissionOut = {
  id: "11111111-1111-1111-1111-111111111111",
  channel: "extension",
  received_at: "2026-10-23T09:00:01Z",
  outcome: "positive",
  status: "processing",
  reason_code: null,
  reason_text: null,
  duplicate_received_at: null,
  fields: null,
  thumb_url: null,
};

describe("разбор ошибок", () => {
  it("классифицирует статусы", () => {
    expect(classifyStatus(401)).toBe("auth");
    expect(classifyStatus(403)).toBe("blocked");
    expect(classifyStatus(426)).toBe("upgrade");
    expect(classifyStatus(429)).toBe("rate");
    for (const status of [400, 404, 409, 413, 415, 422]) expect(classifyStatus(status)).toBe("client");
    for (const status of [0, 408, 500, 502, 503]) expect(classifyStatus(status)).toBe("retry");
  });

  it("401: текст из detail и код из X-Error-Code", async () => {
    const error = await errorFromResponse(errorResponse(401, "Токен расширения не подходит", { "X-Error-Code": "install_revoked" }));
    expect([error.status, error.kind, error.code, error.message]).toEqual([401, "auth", "install_revoked", "Токен расширения не подходит"]);
  });

  it("426: обновить расширение", async () => {
    const error = await errorFromResponse(errorResponse(426, "Обновите расширение до версии 0.2.0", { "X-Error-Code": "upgrade_required" }));
    expect(error.kind).toBe("upgrade");
    expect(error.message).toContain("0.2.0");
  });

  it("429: Retry-After в секундах и в виде даты", async () => {
    expect((await errorFromResponse(errorResponse(429, "Слишком много", { "Retry-After": "120" }))).retryAfterSec).toBe(120);
    const now = Date.parse("2026-10-23T09:00:00Z");
    const dated = await errorFromResponse(errorResponse(429, "x", { "Retry-After": "Fri, 23 Oct 2026 09:05:00 GMT" }), now);
    expect(dated.retryAfterSec).toBe(300);
    expect(parseRetryAfter("ерунда")).toBeNull();
    expect(parseRetryAfter(null)).toBeNull();
    expect(parseRetryAfter("Fri, 23 Oct 2026 08:00:00 GMT", now)).toBe(0);
  });

  it("413/415/422 — ошибки клиента, без Retry-After", async () => {
    const tooLarge = await errorFromResponse(errorResponse(413, "Файл больше 15 МБ", { "X-Error-Code": "file_too_large", "Retry-After": "5" }));
    expect([tooLarge.kind, tooLarge.code, tooLarge.retryAfterSec]).toEqual(["client", "file_too_large", null]);
    const unsupported = await errorFromResponse(errorResponse(415, "Нужен PNG или JPEG", { "X-Error-Code": "unsupported_type" }));
    expect(unsupported.kind).toBe("client");
    const validation = await errorFromResponse(errorResponse(422, [{ loc: ["body", "outcome"], msg: "field required" }]));
    expect([validation.kind, validation.message]).toEqual(["client", "Сервер не принял данные запроса"]);
  });

  it("не-JSON ответ (страница nginx) — запасной текст", async () => {
    const error = await errorFromResponse(new Response("<html>502</html>", { status: 502 }));
    expect([error.kind, error.message]).toEqual(["retry", "Сервис недоступен, отправим позже"]);
  });
});

describe("call", () => {
  it("шлёт токен и версию, а сетевой сбой превращает в retry", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ items: [] }), { status: 200 }));
    await call({ backendUrl: "https://ma.example", token: "mas_x", version: "0.1.0", fetch: fetchMock }, "GET", "/submissions?limit=5");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(`https://ma.example${API_PREFIX}/submissions?limit=5`);
    expect(init.headers).toMatchObject({ Authorization: "Bearer mas_x", "X-Extension-Version": "0.1.0" });

    const offline = vi.fn(async () => Promise.reject(new TypeError("Failed to fetch")));
    const error = (await call({ backendUrl: "https://ma.example", token: "", version: "0.1.0", fetch: offline }, "GET", "/me").catch(
      (e: unknown) => e,
    )) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect([error.kind, error.status]).toEqual(["retry", 0]);
  });

  it("pair идёт без токена и с JSON-телом", async () => {
    const fetchMock = vi.fn(
      async () => new Response(JSON.stringify({ token: "mas_t", username: "anna", min_version: "0.1.0", allowed_url_prefixes: [] }), { status: 200 }),
    );
    const reply = await pair(
      { backendUrl: "https://ma.example", version: "0.1.0", fetch: fetchMock },
      { code: "ABCD2345", install_id: "chrome-1234567890", browser: "Chrome 130", ext_version: "0.1.0" },
    );
    expect(reply.token).toBe("mas_t");
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
    expect(JSON.parse(init.body as string)).toMatchObject({ code: "ABCD2345", install_id: "chrome-1234567890" });
  });

  it("submit отправляет multipart со всеми полями", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(submission), { status: 201 }));
    const result = await submit(
      { backendUrl: "https://ma.example", token: "mas_x", version: "0.1.0", fetch: fetchMock },
      {
        file: new Blob(["png"], { type: "image/png" }),
        filename: "tab.png",
        outcome: "positive",
        clientSubmissionId: "22222222-2222-2222-2222-222222222222",
        clientUrl: "https://seller.wildberries.ru/chat-with-clients?chatId=1",
        clientCapturedAt: "2026-10-23T09:00:00.000Z",
        tzOffsetMin: -180,
        viewportW: 1440,
        viewportH: 900,
        dpr: 2,
      },
    );
    expect(result.id).toBe(submission.id);
    const form = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].body as FormData;
    expect(Object.fromEntries([...form.entries()].filter(([key]) => key !== "file"))).toEqual({
      outcome: "positive",
      client_submission_id: "22222222-2222-2222-2222-222222222222",
      client_url: "https://seller.wildberries.ru/chat-with-clients?chatId=1",
      client_captured_at: "2026-10-23T09:00:00.000Z",
      tz_offset_min: "-180",
      viewport_w: "1440",
      viewport_h: "900",
      dpr: "2",
    });
    expect((form.get("file") as File).name).toBe("tab.png");
  });

  it("необязательные метаданные не отправляются пустыми", () => {
    const form = submissionForm({
      file: new Blob(["x"]),
      filename: "tab.png",
      outcome: "deleted",
      clientSubmissionId: "id",
      clientUrl: "u".repeat(3000),
      clientCapturedAt: "t",
      tzOffsetMin: 0,
      viewportW: null,
      viewportH: null,
      dpr: null,
    });
    expect(form.has("viewport_w")).toBe(false);
    expect(form.has("dpr")).toBe(false);
    expect((form.get("client_url") as string).length).toBe(2048);
    expect(form.get("tz_offset_min")).toBe("0");
  });
});

describe("мелочи", () => {
  it("код сопряжения: Crockford base32, 8 символов", () => {
    expect(normalizePairingCode("abcd2345")).toBe("ABCD2345");
    expect(normalizePairingCode(" ABCD-2345 ")).toBe("ABCD2345");
    expect(normalizePairingCode("OIL12345")).toBe("01112345");
    expect(normalizePairingCode("ABCU2345")).toBeNull();
    expect(normalizePairingCode("123456")).toBeNull();
    expect(normalizePairingCode("ABCD23456")).toBeNull();
  });

  it("статусы по-русски", () => {
    expect(statusText({ status: "processing", reason_text: null })).toBe("В обработке");
    expect(statusText({ status: "accepted", reason_text: null })).toBe("Принят");
    expect(statusText({ status: "duplicate", reason_text: null })).toBe("Уже загружен");
    expect(statusText({ status: "needs_review", reason_text: null })).toBe("Проверяется");
    expect(statusText({ status: "rejected", reason_text: "Не видно даты отзыва" })).toBe("Не прочитан: Не видно даты отзыва");
    expect(isFinal("processing")).toBe(false);
    expect(isFinal("needs_review")).toBe(true);
  });

  it("сегодня — по Москве", () => {
    expect(moscowToday(new Date("2026-10-23T21:30:00Z"))).toBe("2026-10-24");
    expect(moscowToday(new Date("2026-10-23T20:59:00Z"))).toBe("2026-10-23");
  });
});
