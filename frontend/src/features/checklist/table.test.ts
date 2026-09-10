import { describe, expect, it } from "vitest";

import { cellHint, cellTone, notices, visibleRows } from "./table";
import type { Checklist, ChecklistItem, ChecklistRow, ItemState } from "./types";

const characteristics: ChecklistItem = {
  key: "characteristics",
  title: "Характеристики",
  meaning: "Заполнены все характеристики.",
};

function state(overrides: Partial<ItemState>): ItemState {
  return { key: "photos", done: true, detail: null, note: null, ...overrides };
}

function row(article: string, ready: boolean, overrides: Partial<ChecklistRow> = {}): ChecklistRow {
  return {
    article,
    vendor_code: `SKU-${article}`,
    barcode: `20${article}`,
    title: `Товар ${article}`,
    photo_url: "",
    subject_name: "Пилы",
    card_created_at: null,
    stock: 20,
    items: [],
    done: ready ? 8 : 3,
    total: 8,
    ready,
    comment: "",
    ...overrides,
  };
}

function checklist(overrides: Partial<Checklist> = {}): Checklist {
  return {
    seller_id: "seller-1",
    seller_name: "ИП",
    collected_at: null,
    collection_error: null,
    stock_state: "ok",
    reviews_state: "ok",
    min_stock: 10,
    items: [],
    rows: [],
    ...overrides,
  };
}

describe("checklist table", () => {
  it("colours a cell by what WB shows", () => {
    expect(cellTone(state({ done: true }))).toBe("done");
    expect(cellTone(state({ done: false }))).toBe("missing");
    // Нет данных — это «не знаем», а не «не выполнено».
    expect(cellTone(state({ done: null }))).toBe("neutral");
  });

  it("puts WB's figure and the empty fields into the hint", () => {
    const hint = cellHint(
      characteristics,
      state({ key: "characteristics", done: false, detail: "22/25", note: "Не заполнены: Цвет" }),
    );

    expect(hint).toContain("WB: 22/25");
    expect(hint).toContain("Не заполнены: Цвет");
    expect(hint).not.toContain("Данных пока нет");
    expect(cellHint(characteristics, state({ done: null }))).toContain("Данных пока нет");
  });

  it("filters by search and by readiness", () => {
    const rows = [row("111", true), row("222", false, { title: "Пила цепная" })];

    expect(visibleRows(rows, { search: "", onlyPending: true }).map((item) => item.article)).toEqual(["222"]);
    expect(visibleRows(rows, { search: "ПИЛА", onlyPending: false }).map((item) => item.article)).toEqual(["222"]);
    expect(visibleRows(rows, { search: "20111", onlyPending: false }).map((item) => item.article)).toEqual(["111"]);
  });

  it("says why the table is empty", () => {
    expect(notices(checklist({ stock_state: "not_connected", reviews_state: "not_connected" }))).toHaveLength(2);
    expect(notices(checklist())).toEqual([]);
  });
});
