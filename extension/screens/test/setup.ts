import "fake-indexeddb/auto";
import { beforeEach, vi } from "vitest";

/** Минимальный chrome.* для модулей пакета: storage в памяти, остальное — заглушки vi.fn. */
function createChrome() {
  let data: Record<string, unknown> = {};
  const clone = <T>(value: T): T => (value === undefined ? value : structuredClone(value));
  return {
    reset() {
      data = {};
    },
    storage: {
      local: {
        get: vi.fn(async (defaults: Record<string, unknown> | string | string[] | null) => {
          if (defaults && typeof defaults === "object" && !Array.isArray(defaults)) {
            const result: Record<string, unknown> = {};
            for (const [key, fallback] of Object.entries(defaults)) result[key] = clone(key in data ? data[key] : fallback);
            return result;
          }
          return clone(data);
        }),
        set: vi.fn(async (patch: Record<string, unknown>) => {
          data = { ...data, ...clone(patch) };
        }),
      },
      onChanged: { addListener: vi.fn() },
    },
    runtime: {
      getManifest: () => ({ version: "0.1.0" }),
      sendMessage: vi.fn(async () => ({ ok: true })),
      onMessage: { addListener: vi.fn() },
      onInstalled: { addListener: vi.fn() },
      onStartup: { addListener: vi.fn() },
    },
    action: { setBadgeText: vi.fn(async () => undefined), setBadgeBackgroundColor: vi.fn(async () => undefined) },
    alarms: { create: vi.fn(async () => undefined), clear: vi.fn(async () => true), onAlarm: { addListener: vi.fn() } },
  };
}

const chromeMock = createChrome();
(globalThis as unknown as { chrome: unknown }).chrome = chromeMock;

beforeEach(() => {
  chromeMock.reset();
});
