import { IDBFactory } from "fake-indexeddb";
import { describe, expect, it, vi } from "vitest";
import { ApiError, type SubmissionOut } from "../src/api";
import {
  MAX_RETRY_WINDOW_MS,
  afterFailure,
  foreignQueueText,
  idbStore,
  newItem,
  nextWakeAt,
  ownerKey,
  processQueue,
  removeForeign,
  resetForRetry,
  retryDelay,
  type ProcessDeps,
  type QueueItem,
  type SendOutcome,
} from "../src/queue";
import { loadSettings, pauseFor, savePairing, saveSettings } from "../src/settings";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const T0 = Date.parse("2026-10-23T09:00:00Z");
const BACKEND = "https://ma.example.ru";
const ANNA = ownerKey(BACKEND, "anna");
const BORIS = ownerKey(BACKEND, "boris");

function item(id: string, now = T0, owner = ANNA): QueueItem {
  return newItem(
    {
      id,
      owner,
      blob: new Blob([`png-${id}`], { type: "image/png" }),
      filename: "tab.png",
      outcome: "positive",
      url: "https://seller.wildberries.ru/chat-with-clients?chatId=1",
      capturedAt: new Date(now).toISOString(),
      tzOffsetMin: -180,
      viewportW: 1440,
      viewportH: 900,
      dpr: 2,
    },
    now,
  );
}

function submission(id: string): SubmissionOut {
  return {
    id,
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
}

function harness(
  items: QueueItem[],
  responses: SendOutcome[],
  options: { paused?: boolean; now?: number; owner?: string | null } = {},
) {
  const store = idbStore(new IDBFactory());
  let clock = options.now ?? T0;
  const send = vi.fn(async (_item: QueueItem): Promise<SendOutcome> => responses.shift() ?? { ok: true, submission: submission("s") });
  const deps: ProcessDeps = {
    store,
    now: () => clock,
    isPaused: async () => Boolean(options.paused),
    owner: async () => (options.owner === undefined ? ANNA : options.owner),
    send,
    pause: vi.fn(async () => undefined),
    onSent: vi.fn(),
    onDropped: vi.fn(),
    onRetry: vi.fn(),
  };
  return {
    store,
    deps,
    send,
    setNow: (value: number) => {
      clock = value;
    },
    async seed() {
      for (const entry of items) await store.put(entry);
    },
  };
}

const fail = (status: number, kind: ApiError["kind"], retryAfterSec: number | null = null): SendOutcome => ({
  ok: false,
  error: new ApiError(`ошибка ${status}`, status, kind, null, retryAfterSec),
});

describe("расписание повторов", () => {
  it("1 мин, 5 мин, 15 мин, 1 ч, дальше раз в час", () => {
    expect([1, 2, 3, 4, 5, 6, 40].map(retryDelay)).toEqual([MINUTE, 5 * MINUTE, 15 * MINUTE, HOUR, HOUR, HOUR, HOUR]);
  });

  it("последовательные неудачи сдвигают nextAttemptAt по расписанию", () => {
    let current = item("a");
    const seen: number[] = [];
    let now = T0;
    for (let i = 0; i < 6; i += 1) {
      current = afterFailure(current, now, "нет связи");
      seen.push(current.nextAttemptAt - now);
      now = current.nextAttemptAt;
    }
    expect(seen).toEqual([MINUTE, 5 * MINUTE, 15 * MINUTE, HOUR, HOUR, HOUR]);
    expect(current.state).toBe("pending");
    expect(current.attempts).toBe(6);
  });

  it("через 72 часа запись уходит в «не отправлено» и остаётся", () => {
    let current = item("a");
    let now = T0;
    while (current.state === "pending") {
      now = current.nextAttemptAt;
      current = afterFailure(current, now, "нет связи");
    }
    expect(current.state).toBe("failed");
    expect(now - T0).toBeLessThanOrEqual(MAX_RETRY_WINDOW_MS);
    expect(now - T0).toBeGreaterThan(MAX_RETRY_WINDOW_MS - 2 * HOUR);
    expect(nextWakeAt([current], ANNA)).toBeNull();
  });

  it("Retry-After больше шага расписания — ждём сколько сказали", () => {
    expect(afterFailure(item("a"), T0, "429", 600).nextAttemptAt).toBe(T0 + 600_000);
    expect(afterFailure(item("a"), T0, "429", 5).nextAttemptAt).toBe(T0 + MINUTE);
  });

  it("«Повторить» открывает новое окно в 72 часа", () => {
    const failed = { ...item("a"), state: "failed" as const, attempts: 70, lastError: "x" };
    const reset = resetForRetry(failed, T0 + 100 * HOUR);
    expect(reset).toMatchObject({ state: "pending", attempts: 0, windowStart: T0 + 100 * HOUR, nextAttemptAt: T0 + 100 * HOUR, lastError: null });
  });
});

describe("processQueue", () => {
  it("успех — запись удаляется из IndexedDB", async () => {
    const h = harness([item("a")], [{ ok: true, submission: submission("srv-a") }]);
    await h.seed();
    const results = await processQueue(h.deps);
    expect(results).toEqual([{ id: "a", state: "sent", submission: submission("srv-a") }]);
    expect(await h.store.all()).toEqual([]);
    expect(h.deps.onSent).toHaveBeenCalledOnce();
  });

  it("413/415/422 — не повторяем, удаляем и сообщаем причину", async () => {
    const h = harness([item("a"), item("b"), item("c")], [fail(413, "client"), fail(415, "client"), fail(422, "client")]);
    await h.seed();
    const results = await processQueue(h.deps);
    expect(results.map((r) => r.state)).toEqual(["dropped", "dropped", "dropped"]);
    expect(await h.store.all()).toEqual([]);
    expect(h.deps.onDropped).toHaveBeenCalledTimes(3);
  });

  it("401 — пауза очереди, запись остаётся без траты попытки, остальные не отправляются", async () => {
    const h = harness([item("a", T0 - 2), item("b", T0 - 1)], [fail(401, "auth")]);
    await h.seed();
    const results = await processQueue(h.deps);
    expect(results).toEqual([{ id: "a", state: "paused", error: "ошибка 401" }]);
    expect(h.send).toHaveBeenCalledTimes(1);
    expect(h.deps.pause).toHaveBeenCalledOnce();
    const stored = await h.store.all();
    expect(stored.map((entry) => [entry.id, entry.attempts, entry.state])).toEqual([
      ["a", 0, "pending"],
      ["b", 0, "pending"],
    ]);
  });

  it("426 и 403 тоже ставят на паузу", async () => {
    for (const [status, kind] of [[426, "upgrade"], [403, "blocked"]] as const) {
      const h = harness([item("a")], [fail(status, kind)]);
      await h.seed();
      expect((await processQueue(h.deps))[0].state).toBe("paused");
      expect(await h.store.all()).toHaveLength(1);
    }
  });

  it("на паузе ничего не отправляется", async () => {
    const h = harness([item("a")], [], { paused: true });
    await h.seed();
    expect(await processQueue(h.deps)).toEqual([]);
    expect(h.send).not.toHaveBeenCalled();
  });

  it("сеть или 5xx — повтор через минуту, до срока запись не трогаем", async () => {
    const h = harness([item("a")], [fail(0, "retry"), fail(503, "retry")]);
    await h.seed();
    expect((await processQueue(h.deps))[0].state).toBe("queued");
    const [stored] = await h.store.all();
    expect([stored.attempts, stored.nextAttemptAt]).toEqual([1, T0 + MINUTE]);
    expect(nextWakeAt(await h.store.all(), ANNA)).toBe(T0 + MINUTE);

    h.setNow(T0 + 30_000);
    expect(await processQueue(h.deps)).toEqual([]);
    h.setNow(T0 + MINUTE);
    await processQueue(h.deps);
    const [again] = await h.store.all();
    expect([again.attempts, again.nextAttemptAt]).toEqual([2, T0 + MINUTE + 5 * MINUTE]);
  });

  it("429 — ждём Retry-After и сдвигаем остальные записи", async () => {
    const h = harness([item("a", T0 - 2), item("b", T0 - 1)], [fail(429, "rate", 300)]);
    await h.seed();
    const results = await processQueue(h.deps);
    expect(h.send).toHaveBeenCalledTimes(1);
    expect(results.map((r) => r.state)).toEqual(["queued", "queued"]);
    const stored = await h.store.all();
    expect(stored.map((entry) => entry.nextAttemptAt)).toEqual([T0 + 300_000, T0 + 300_000]);
    expect(stored.map((entry) => entry.attempts)).toEqual([1, 0]);
  });

  it("запись старше 72 часов уходит в «не отправлено» без отправки", async () => {
    const h = harness([item("a")], [], { now: T0 + MAX_RETRY_WINDOW_MS });
    await h.seed();
    const results = await processQueue(h.deps);
    expect(results[0].state).toBe("failed");
    expect(h.send).not.toHaveBeenCalled();
    const [stored] = await h.store.all();
    expect(stored.state).toBe("failed");
    expect(await processQueue(h.deps)).toEqual([]);
  });
});

describe("очередь и сотрудник", () => {
  it("отправляются только записи подключённого сотрудника", async () => {
    const legacy = { ...item("old", T0 - 3) };
    delete legacy.owner;
    const h = harness([legacy, item("a", T0 - 2, ANNA), item("b", T0 - 1, BORIS)], [], { owner: BORIS });
    await h.seed();
    const results = await processQueue(h.deps);
    expect(results.map((r) => [r.id, r.state])).toEqual([["b", "sent"]]);
    expect(h.send.mock.calls.map(([sent]) => sent.id)).toEqual(["b"]);
    expect((await h.store.all()).map((entry) => entry.id)).toEqual(["old", "a"]);
  });

  it("без подключённого сотрудника не уходит ничего, будить незачем", async () => {
    const h = harness([item("a")], [], { owner: null });
    await h.seed();
    expect(await processQueue(h.deps)).toEqual([]);
    expect(h.send).not.toHaveBeenCalled();
    expect(nextWakeAt(await h.store.all(), null)).toBeNull();
    expect(nextWakeAt(await h.store.all(), BORIS)).toBeNull();
    expect(nextWakeAt(await h.store.all(), ANNA)).toBe(T0);
  });

  it("смена сотрудника: записи прежнего остаются за ним, новый их не отправляет", async () => {
    const pairReply = (username: string) => ({ token: `mas_${username}`, username, min_version: "0.1.0", allowed_url_prefixes: [] });
    expect(await savePairing(BACKEND, pairReply("anna"))).toEqual({ owner: ANNA, changed: false });
    await saveSettings({ recent: [{ id: "a", outcome: "positive", capturedAt: "x", state: "queued", serverId: null, status: null, reasonText: null, error: null }] });

    // «Отключить» стирает токен, но не владельца: тот же сотрудник продолжит свою очередь.
    await saveSettings({ token: "", username: "", pause: null });
    expect((await loadSettings()).owner).toBe(ANNA);
    expect(await savePairing(BACKEND, pairReply("anna"))).toEqual({ owner: ANNA, changed: false });
    expect((await loadSettings()).recent).toHaveLength(1);

    const result = await savePairing(BACKEND, pairReply("boris"));
    expect(result).toEqual({ owner: BORIS, changed: true });
    const settings = await loadSettings();
    expect([settings.owner, settings.token, settings.recent]).toEqual([BORIS, "mas_boris", []]);

    const h = harness([item("a", T0 - 1, ANNA)], [], { owner: settings.owner });
    await h.seed();
    expect(await processQueue(h.deps)).toEqual([]);
    expect(h.send).not.toHaveBeenCalled();
  });

  it("тот же логин на другом сервисе — другой сотрудник", () => {
    expect(ownerKey("https://a.example.ru", "anna")).not.toBe(ownerKey("https://b.example.ru", "anna"));
  });

  it("сопряжение старой версии без владельца: владелец — сохранённый сотрудник", async () => {
    await saveSettings({ backendUrl: BACKEND, token: "mas_x", username: "anna" });
    expect((await loadSettings()).owner).toBe(ANNA);
  });

  it("«Удалить» убирает только чужие записи", async () => {
    const store = idbStore(new IDBFactory());
    for (const entry of [item("a", T0, ANNA), item("b", T0 + 1, BORIS), item("c", T0 + 2, ANNA)]) await store.put(entry);
    expect(await removeForeign(store, BORIS)).toBe(2);
    expect((await store.all()).map((entry) => entry.id)).toEqual(["b"]);
  });

  it("текст про чужие записи склоняется", () => {
    expect([1, 2, 5, 11, 21].map(foreignQueueText)).toEqual([
      "В очереди 1 скриншот другого сотрудника",
      "В очереди 2 скриншота другого сотрудника",
      "В очереди 5 скриншотов другого сотрудника",
      "В очереди 11 скриншотов другого сотрудника",
      "В очереди 21 скриншот другого сотрудника",
    ]);
  });
});

describe("IndexedDB", () => {
  it("хранит blob и метаданные между открытиями", async () => {
    const factory = new IDBFactory();
    await idbStore(factory).put(item("a"));
    const reopened = idbStore(factory);
    const stored = await reopened.get("a");
    expect(stored?.outcome).toBe("positive");
    expect(stored?.blob.size).toBe("png-a".length);
    await reopened.remove("a");
    expect(await reopened.all()).toEqual([]);
  });
});

describe("pauseFor", () => {
  it("401 стирает токен, 426 оставляет", async () => {
    await saveSettings({ token: "mas_x" });
    await pauseFor(new ApiError("Обновите", 426, "upgrade"));
    expect((await loadSettings()).token).toBe("mas_x");
    expect((await loadSettings()).pause?.kind).toBe("upgrade");
    await pauseFor(new ApiError("Токен не подходит", 401, "auth"));
    const settings = await loadSettings();
    expect([settings.token, settings.pause?.kind]).toEqual(["", "auth"]);
    expect(await pauseFor(new ApiError("x", 413, "client"))).toBe(false);
  });
});
