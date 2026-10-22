/**
 * Очередь отправки в IndexedDB. Один захват — одна запись, ключ — client_submission_id, поэтому
 * повтор после обрыва безопасен: сервер отвечает 200 и возвращает тот же сабмишен.
 * Service worker засыпает, так что в памяти ничего не держится: расписание — поле nextAttemptAt,
 * будит chrome.alarms.
 */
import type { ApiError, Outcome, SubmissionOut } from "./api";

const MINUTE = 60_000;
/** 1 мин, 5 мин, 15 мин, 1 ч, дальше раз в час. */
export const RETRY_DELAYS_MS = [1 * MINUTE, 5 * MINUTE, 15 * MINUTE, 60 * MINUTE] as const;
export const MAX_RETRY_WINDOW_MS = 72 * 60 * MINUTE;

export interface QueueItem {
  id: string;
  blob: Blob;
  filename: string;
  outcome: Outcome;
  url: string;
  capturedAt: string;
  tzOffsetMin: number;
  viewportW: number | null;
  viewportH: number | null;
  dpr: number | null;
  createdAt: number;
  /** Начало окна в 72 часа; «Повторить» в настройках открывает новое окно. */
  windowStart: number;
  attempts: number;
  nextAttemptAt: number;
  state: "pending" | "failed";
  lastError: string | null;
}

export type NewItem = Omit<QueueItem, "createdAt" | "windowStart" | "attempts" | "nextAttemptAt" | "state" | "lastError">;

export function newItem(fields: NewItem, now: number): QueueItem {
  return { ...fields, createdAt: now, windowStart: now, attempts: 0, nextAttemptAt: now, state: "pending", lastError: null };
}

export function retryDelay(attempts: number): number {
  return RETRY_DELAYS_MS[Math.min(Math.max(attempts, 1), RETRY_DELAYS_MS.length) - 1];
}

/**
 * Неудачная попытка (сеть, 5xx, 429). Окно 72 ч от windowStart: если следующая попытка в него не
 * влезает, запись становится «не отправлено» и ждёт ручного решения.
 */
export function afterFailure(item: QueueItem, now: number, error: string, retryAfterSec: number | null = null): QueueItem {
  const attempts = item.attempts + 1;
  const delay = Math.max(retryDelay(attempts), (retryAfterSec ?? 0) * 1000);
  const next = now + delay;
  const deadline = item.windowStart + MAX_RETRY_WINDOW_MS;
  if (next > deadline) return { ...item, attempts, state: "failed", lastError: error, nextAttemptAt: next };
  return { ...item, attempts, state: "pending", lastError: error, nextAttemptAt: next };
}

export function resetForRetry(item: QueueItem, now: number): QueueItem {
  return { ...item, state: "pending", attempts: 0, windowStart: now, nextAttemptAt: now, lastError: null };
}

// --- хранилище ---

export interface QueueStore {
  put(item: QueueItem): Promise<void>;
  get(id: string): Promise<QueueItem | undefined>;
  all(): Promise<QueueItem[]>;
  remove(id: string): Promise<void>;
}

const DB_NAME = "marketplace-auto-screens";
const STORE = "queue";

function promisify<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export function openDb(factory: IDBFactory = indexedDB): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = factory.open(DB_NAME, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE)) request.result.createObjectStore(STORE, { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export function idbStore(factory: IDBFactory = indexedDB): QueueStore {
  let db: Promise<IDBDatabase> | null = null;
  const run = async <T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> => {
    db ??= openDb(factory);
    const transaction = (await db).transaction(STORE, mode);
    const result = await promisify(action(transaction.objectStore(STORE)));
    await new Promise<void>((resolve, reject) => {
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
    return result;
  };
  return {
    async put(item) {
      await run("readwrite", (store) => store.put(item));
    },
    get: (id) => run("readonly", (store) => store.get(id) as IDBRequest<QueueItem | undefined>),
    all: async () => ((await run("readonly", (store) => store.getAll())) as QueueItem[]).sort((a, b) => a.createdAt - b.createdAt),
    async remove(id) {
      await run("readwrite", (store) => store.delete(id));
    },
  };
}

// --- обработка ---

export type SendOutcome = { ok: true; submission: SubmissionOut } | { ok: false; error: ApiError };

export interface ProcessDeps {
  store: QueueStore;
  now: () => number;
  /** Очередь на паузе (нет токена, 401/403/426) — ничего не отправляем. */
  isPaused: () => Promise<boolean>;
  send: (item: QueueItem) => Promise<SendOutcome>;
  pause: (error: ApiError) => Promise<void>;
  onSent?: (item: QueueItem, submission: SubmissionOut) => Promise<void> | void;
  onDropped?: (item: QueueItem, error: ApiError) => Promise<void> | void;
  onRetry?: (item: QueueItem) => Promise<void> | void;
}

export interface ItemResult {
  id: string;
  state: "sent" | "queued" | "failed" | "dropped" | "paused";
  submission?: SubmissionOut;
  error?: string;
}

/**
 * Отправляет всё, чему подошёл срок. 401/403/426 ставят очередь на паузу без траты попытки;
 * прочие 4xx удаляют запись (повтор бесполезен); сеть, 5xx и 429 — повтор по расписанию,
 * 429 сдвигает и остальные записи на Retry-After.
 */
export async function processQueue(deps: ProcessDeps): Promise<ItemResult[]> {
  const results: ItemResult[] = [];
  if (await deps.isPaused()) return results;
  const items = await deps.store.all();
  let holdUntil = 0;
  for (const item of items) {
    if (item.state !== "pending") continue;
    const now = deps.now();
    if (item.nextAttemptAt > now) continue;
    if (now >= item.windowStart + MAX_RETRY_WINDOW_MS) {
      const expired: QueueItem = { ...item, state: "failed", lastError: item.lastError ?? "Не отправлено за 72 часа" };
      await deps.store.put(expired);
      results.push({ id: item.id, state: "failed", error: expired.lastError ?? undefined });
      await deps.onRetry?.(expired);
      continue;
    }
    if (holdUntil > now) {
      await deps.store.put({ ...item, nextAttemptAt: Math.max(item.nextAttemptAt, holdUntil) });
      results.push({ id: item.id, state: "queued" });
      continue;
    }
    const outcome = await deps.send(item);
    if (outcome.ok) {
      await deps.store.remove(item.id);
      await deps.onSent?.(item, outcome.submission);
      results.push({ id: item.id, state: "sent", submission: outcome.submission });
      continue;
    }
    const { error } = outcome;
    if (error.kind === "auth" || error.kind === "blocked" || error.kind === "upgrade") {
      await deps.pause(error);
      results.push({ id: item.id, state: "paused", error: error.message });
      break;
    }
    if (error.kind === "client") {
      await deps.store.remove(item.id);
      await deps.onDropped?.(item, error);
      results.push({ id: item.id, state: "dropped", error: error.message });
      continue;
    }
    const updated = afterFailure(item, deps.now(), error.message, error.retryAfterSec);
    await deps.store.put(updated);
    await deps.onRetry?.(updated);
    results.push({ id: item.id, state: updated.state === "failed" ? "failed" : "queued", error: error.message });
    if (error.kind === "rate") holdUntil = updated.nextAttemptAt;
  }
  return results;
}

/** Когда будить worker в следующий раз: ближайшая попытка среди ожидающих. */
export function nextWakeAt(items: QueueItem[]): number | null {
  const pending = items.filter((item) => item.state === "pending").map((item) => item.nextAttemptAt);
  return pending.length ? Math.min(...pending) : null;
}
