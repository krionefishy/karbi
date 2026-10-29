/**
 * Клиент `/api/v1/wb/screenshot-control/extension/*`. Типы повторяют
 * backend/modules/wb_screenshot_control/presentation/http/schemas.py вручную.
 * Ошибка сервера — `{"detail": "..."}`, машинный код — заголовок `X-Error-Code`.
 */

export const API_PREFIX = "/api/v1/wb/screenshot-control/extension";

export type Outcome = "positive" | "amended" | "deleted";
export type Status = "awaiting_outcome" | "processing" | "accepted" | "duplicate" | "needs_review" | "rejected";
export type Mode = "off" | "shadow" | "enforce";

export const OUTCOME_CAPTIONS: Record<Outcome, string> = { positive: "полож", amended: "доп", deleted: "удален" };

export interface SubmissionOut {
  id: string;
  channel: "extension" | "telegram" | "web";
  received_at: string;
  outcome: Outcome | null;
  status: Status;
  reason_code: string | null;
  reason_text: string | null;
  duplicate_received_at: string | null;
  fields: unknown;
  thumb_url: string | null;
}

export interface Summary {
  period: { from: string; to: string };
  mode: Mode;
  preliminary: boolean;
  counts: { positive: number; amended: number; deleted: number; accepted_total: number };
  pending_review: number;
  awaiting_outcome: number;
  processing: number;
  duplicates: number;
  rejected: number;
}

export interface PairOut {
  token: string;
  username: string;
  min_version: string;
  allowed_url_prefixes: string[];
}

export interface MeOut {
  username: string;
  min_version: string;
  mode: Mode;
  allowed_url_prefixes: string[];
  summary: Summary;
  server_time: string;
}

/**
 * Как обходиться с ошибкой:
 * auth — токен не годится (401), очередь на паузу до нового сопряжения;
 * blocked — сотрудник заблокирован (403), тоже пауза;
 * upgrade — версия ниже минимальной (426), пауза и «обновите расширение»;
 * rate — 429, ждать Retry-After;
 * client — прочие 4xx (413/415/422/409/404…), повтор бесполезен;
 * retry — сеть, 5xx, 408: повтор по расписанию.
 */
export type ErrorKind = "auth" | "blocked" | "upgrade" | "rate" | "client" | "retry";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly kind: ErrorKind,
    readonly code: string | null = null,
    readonly retryAfterSec: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function classifyStatus(status: number): ErrorKind {
  if (status === 0 || status >= 500 || status === 408) return "retry";
  if (status === 401) return "auth";
  if (status === 403) return "blocked";
  if (status === 426) return "upgrade";
  if (status === 429) return "rate";
  return "client";
}

/** Retry-After: секунды или HTTP-дата. Непонятное значение — null. */
export function parseRetryAfter(value: string | null, now: number = Date.now()): number | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Number(trimmed);
  const at = Date.parse(trimmed);
  if (Number.isNaN(at)) return null;
  return Math.max(0, Math.ceil((at - now) / 1000));
}

const FALLBACK_MESSAGES: Record<ErrorKind, string> = {
  auth: "Расширение отключено от сервиса: подключите его заново по коду со страницы",
  blocked: "Доступ сотрудника заблокирован",
  upgrade: "Обновите расширение",
  rate: "Слишком много запросов, попробуйте позже",
  client: "Сервер не принял скриншот",
  retry: "Сервис недоступен, отправим позже",
};

export async function errorFromResponse(response: Response, now: number = Date.now()): Promise<ApiError> {
  const kind = classifyStatus(response.status);
  let detail = "";
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
    else if (Array.isArray(body.detail)) detail = "Сервер не принял данные запроса";
  } catch {
    // тело не JSON — например, страница nginx
  }
  const retryAfter = kind === "rate" || kind === "retry" ? parseRetryAfter(response.headers.get("Retry-After"), now) : null;
  return new ApiError(detail || FALLBACK_MESSAGES[kind], response.status, kind, response.headers.get("X-Error-Code"), retryAfter);
}

export interface ClientConfig {
  backendUrl: string;
  token: string;
  version: string;
  fetch?: typeof fetch;
}

export async function call<T>(config: ClientConfig, method: string, path: string, body?: BodyInit, json?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-Extension-Version": config.version, Accept: "application/json" };
  if (config.token) headers.Authorization = `Bearer ${config.token}`;
  let payload = body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(json);
  }
  const doFetch = config.fetch ?? fetch;
  let response: Response;
  try {
    response = await doFetch(`${config.backendUrl}${API_PREFIX}${path}`, { method, headers, body: payload, credentials: "omit" });
  } catch {
    throw new ApiError("Нет связи с сервисом", 0, "retry");
  }
  if (!response.ok) throw await errorFromResponse(response);
  return (await response.json()) as T;
}

export interface PairRequest {
  code: string;
  install_id: string;
  browser: string;
  ext_version: string;
}

export function pair(config: Omit<ClientConfig, "token">, request: PairRequest): Promise<PairOut> {
  return call<PairOut>({ ...config, token: "" }, "POST", "/pair", undefined, request);
}

export function me(config: ClientConfig): Promise<MeOut> {
  return call<MeOut>(config, "GET", "/me");
}

export function summary(config: ClientConfig, day: string): Promise<Summary> {
  return call<Summary>(config, "GET", `/summary?from=${day}&to=${day}`);
}

export async function listSubmissions(config: ClientConfig, limit: number): Promise<SubmissionOut[]> {
  return (await call<{ items: SubmissionOut[] }>(config, "GET", `/submissions?limit=${limit}`)).items;
}

export function getSubmission(config: ClientConfig, id: string): Promise<SubmissionOut> {
  return call<SubmissionOut>(config, "GET", `/submissions/${encodeURIComponent(id)}`);
}

export interface SubmitFields {
  file: Blob;
  filename: string;
  outcome: Outcome;
  clientSubmissionId: string;
  clientUrl: string;
  clientCapturedAt: string;
  tzOffsetMin: number;
  viewportW: number | null;
  viewportH: number | null;
  dpr: number | null;
}

export function submissionForm(fields: SubmitFields): FormData {
  const form = new FormData();
  form.set("file", fields.file, fields.filename);
  form.set("outcome", fields.outcome);
  form.set("client_submission_id", fields.clientSubmissionId);
  form.set("client_url", fields.clientUrl.slice(0, 2048));
  form.set("client_captured_at", fields.clientCapturedAt);
  form.set("tz_offset_min", String(fields.tzOffsetMin));
  if (fields.viewportW) form.set("viewport_w", String(fields.viewportW));
  if (fields.viewportH) form.set("viewport_h", String(fields.viewportH));
  if (fields.dpr) form.set("dpr", String(fields.dpr));
  return form;
}

/** 201 — новый, 200 — повтор того же client_submission_id (безопасно). */
export function submit(config: ClientConfig, fields: SubmitFields): Promise<SubmissionOut> {
  return call<SubmissionOut>(config, "POST", "/submissions", submissionForm(fields));
}

/** Сегодняшняя дата по Москве: отчётные периоды сервер считает по Москве. */
export function moscowToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Moscow", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

export function statusText(item: Pick<SubmissionOut, "status" | "reason_text">): string {
  switch (item.status) {
    case "processing":
    case "awaiting_outcome":
      return "В обработке";
    case "accepted":
      return "Принят";
    case "duplicate":
      return "Уже загружен";
    case "needs_review":
      return "Проверяется";
    case "rejected":
      return `Не прочитан${item.reason_text ? `: ${item.reason_text}` : ""}`;
  }
}

export function isFinal(status: Status): boolean {
  return status !== "processing" && status !== "awaiting_outcome";
}

export const PAIRING_CODE_PATTERN = /^[0-9A-HJKMNP-TV-Z]{8}$/;

/**
 * Код сопряжения — 8 символов Crockford base32 без I, L, O, U. Переписывают с экрана руками,
 * поэтому пробелы и дефисы убираются, регистр не важен, а O/I/L читаются как 0/1/1.
 * Неподходящий код — null.
 */
export function normalizePairingCode(raw: string): string | null {
  const code = raw
    .toUpperCase()
    .replace(/[\s-]+/g, "")
    .replace(/O/g, "0")
    .replace(/[IL]/g, "1");
  return PAIRING_CODE_PATTERN.test(code) ? code : null;
}
