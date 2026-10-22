import { describe, expect, it } from "vitest";

import {
  daysLeftText,
  daysTone,
  documentPeriod,
  emptyMessage,
  notices,
  reasonLabel,
  stateTone,
  tiles,
  visibleItems,
} from "./table";
import type { CardDocumentItem, CardDocuments, CardDocumentsSummary } from "./types";

const summary: CardDocumentsSummary = { total: 5, ok: 2, rejected: 1, missing: 0, expiring: 1, mismatch: 1 };

function item(nmId: number, overrides: Partial<CardDocumentItem> = {}): CardDocumentItem {
  return {
    nm_id: nmId,
    vendor_code: `SKU-${nmId}`,
    title: `Товар ${nmId}`,
    subject_name: "Воздуходувки",
    state: "ok",
    reason: "valid",
    document_number: `ЕАЭС N RU Д-${nmId}`,
    document_start: "2025-01-10",
    document_end: "2027-01-09",
    days_left: null,
    checked_at: null,
    since: null,
    ...overrides,
  };
}

function data(overrides: Partial<CardDocuments> = {}): CardDocuments {
  return { collected_at: "2026-10-22T07:00:00Z", last_error: null, stale: false, summary, items: [], ...overrides };
}

const moment = (iso: string) => `[${iso}]`;

describe("card documents table", () => {
  it("names WB reasons in Russian and keeps unknown ones as they came", () => {
    expect(reasonLabel("document_not_found")).toBe("Документ не найден в реестре");
    expect(reasonLabel("documents_missing")).toBe("Документ не загружен");
    expect(reasonLabel("documents_invalid")).toBe("Документ признан недействительным");
    expect(reasonLabel("valid")).toBe("Документ действителен");
    expect(reasonLabel("expired")).toBe("Срок действия истёк");
    expect(reasonLabel("number_mismatch")).toBe("Номер в характеристиках не совпадает с документом");
    expect(reasonLabel("start_date_mismatch")).toContain("Дата регистрации");
    expect(reasonLabel("end_date_mismatch")).toContain("Дата окончания");
    expect(reasonLabel("status_2")).toContain("причину не указал");
    expect(reasonLabel("wrong_period")).toBe("wrong_period");
    expect(reasonLabel(null)).toBe("—");
  });

  it("colours states and the days left", () => {
    expect(stateTone("rejected")).toBe("negative");
    expect(stateTone("expiring")).toBe("warning");
    expect(stateTone("ok")).toBe("positive");
    expect(daysTone(5)).toBe("negative");
    expect(daysTone(14)).toBe("warning");
    expect(daysTone(90)).toBe("neutral");
    expect(daysTone(null)).toBe("neutral");
  });

  it("says how long the document lives", () => {
    expect(daysLeftText(12)).toBe("осталось 12 дн.");
    expect(daysLeftText(0)).toBe("истекает сегодня");
    expect(daysLeftText(-3)).toBe("истёк 3 дн. назад");
    expect(daysLeftText(null)).toBe("—");
    expect(documentPeriod("2025-01-10", "2027-01-09")).toBe("10.01.2025 – 09.01.2027");
    expect(documentPeriod("2025-01-10", null)).toBe("с 10.01.2025, бессрочно");
    expect(documentPeriod(null, null)).toBe("");
  });

  it("counts problems for the first tile", () => {
    const counts = Object.fromEntries(tiles(summary).map((tile) => [tile.key, tile.count]));
    expect(counts).toMatchObject({ problems: 3, rejected: 1, ok: 2, all: 5 });
  });

  it("filters by tile and search keeping the backend order", () => {
    const items = [
      item(3, { state: "rejected", reason: "document_not_found" }),
      item(1, { state: "expiring", days_left: 7 }),
      item(2),
    ];

    expect(visibleItems(items, "problems", "").map((row) => row.nm_id)).toEqual([3, 1]);
    expect(visibleItems(items, "all", "").map((row) => row.nm_id)).toEqual([3, 1, 2]);
    expect(visibleItems(items, "ok", "").map((row) => row.nm_id)).toEqual([2]);
    expect(visibleItems(items, "all", "sku-1").map((row) => row.nm_id)).toEqual([1]);
    expect(visibleItems(items, "all", "Д-3").map((row) => row.nm_id)).toEqual([3]);
  });

  it("warns when watching stopped and when the last collection failed", () => {
    expect(notices(data(), moment)).toEqual([]);

    const stale = notices(data({ stale: true }), moment);
    expect(stale[0].title).toBe("Наблюдение не работает");
    expect(stale[0].text).toContain("[2026-10-22T07:00:00Z]");

    const failed = notices(data({ last_error: "WB ответил 401" }), moment);
    expect(failed).toEqual([expect.objectContaining({ tone: "error", text: expect.stringContaining("WB ответил 401") })]);
  });

  it("explains an empty table", () => {
    expect(emptyMessage("problems", summary, "")).toContain("у всех 5 карточек");
    expect(emptyMessage("problems", { ...summary, total: 0 }, "")).toContain("нет карточек с документами");
    expect(emptyMessage("rejected", summary, "zzz")).toBe("По запросу карточек не нашлось.");
  });
});
