import { describe, expect, it } from "vitest";

import { applyMark, cellHint, isLocked, notices, visibleRows } from "./table";
import type { Checklist, ChecklistRow, ItemState } from "./types";

function state(overrides: Partial<ItemState>): ItemState {
  return {
    key: "cashback",
    kind: "manual",
    checked: false,
    can_check: true,
    detail: null,
    note: null,
    warning: null,
    unknown: false,
    ...overrides,
  };
}

function row(article: string, items: ItemState[], overrides: Partial<ChecklistRow> = {}): ChecklistRow {
  const done = items.filter((item) => item.checked).length;
  return {
    article,
    vendor_code: `SKU-${article}`,
    barcode: `20${article}`,
    title: `Товар ${article}`,
    photo_url: "",
    subject_name: "Пилы",
    card_created_at: null,
    stock: 20,
    items,
    done,
    ready: done === items.length,
    comment: "",
    ...overrides,
  };
}

function checklist(rows: ChecklistRow[], overrides: Partial<Checklist> = {}): Checklist {
  return {
    seller_id: "seller-1",
    seller_name: "ИП",
    collected_at: null,
    collection_error: null,
    stock_state: "ok",
    reviews_state: "ok",
    min_stock: 10,
    items: [],
    rows,
    ...overrides,
  };
}

describe("checklist table", () => {
  it("recounts the row when a tick lands", () => {
    const data = checklist([
      row("1", [state({ key: "photos", kind: "auto", checked: true, can_check: false }), state({ key: "cashback" })]),
    ]);

    const [updated] = applyMark(data, "1", "cashback", true).rows;

    expect(updated.done).toBe(2);
    expect(updated.ready).toBe(true);
  });

  it("drops the stale-fact warning together with the tick", () => {
    const data = checklist([
      row("1", [state({ key: "reviews_with_photo", kind: "confirm", checked: true, warning: "факт пропал" })]),
    ]);

    const [updated] = applyMark(data, "1", "reviews_with_photo", false).rows;

    expect(updated.items[0]).toMatchObject({ checked: false, warning: null });
  });

  it("filters by search and by readiness", () => {
    const rows = [
      row("111", [state({ checked: true })]),
      row("222", [state({ checked: false })], { title: "Пила цепная" }),
    ];

    expect(visibleRows(rows, { search: "", onlyPending: true }).map((item) => item.article)).toEqual(["222"]);
    expect(visibleRows(rows, { search: "ПИЛА", onlyPending: false }).map((item) => item.article)).toEqual(["222"]);
    expect(visibleRows(rows, { search: "20111", onlyPending: false }).map((item) => item.article)).toEqual(["111"]);
  });

  it("locks what WB decides and what has no fact behind it", () => {
    expect(isLocked(state({ kind: "auto", checked: true, can_check: false }))).toBe(true);
    expect(isLocked(state({ kind: "confirm", checked: false, can_check: false }))).toBe(true);
    // Свою отметку снять можно, даже если факта уже нет.
    expect(isLocked(state({ kind: "confirm", checked: true, can_check: false }))).toBe(false);
    expect(isLocked(state({ kind: "manual" }))).toBe(false);
  });

  it("explains a locked cell in its hint", () => {
    const item = { key: "reviews_with_video", title: "Отзывы с видео", kind: "confirm" as const, meaning: "Есть видео-отзывы." };

    const hint = cellHint(item, state({ kind: "confirm", can_check: false, detail: "0 с видео" }));

    expect(hint).toContain("WB: 0 с видео");
    expect(hint).toContain("отмечать пока не на чем");
  });

  it("says why the table is empty", () => {
    expect(notices(checklist([], { stock_state: "not_connected", reviews_state: "not_connected" }))).toHaveLength(2);
    expect(notices(checklist([]))).toEqual([]);
  });
});
