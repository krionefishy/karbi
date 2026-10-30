import { describe, expect, it } from "vitest";

import { boardNotices, daysText, importSummary, sortBoardRows, visibleBoardRows, type Board, type BoardRow } from "./board";

function row(barcode: string, patch: Partial<BoardRow> = {}): BoardRow {
  return {
    seller_id: "s1",
    seller_name: "Кабинет",
    barcode,
    article: "100",
    vendor_code: "KARBI - Пила",
    name: "Пила",
    nomenclature: "",
    note: "",
    in_catalog: true,
    stock: 10,
    orders: 14,
    rate: 2,
    days: 5,
    status: "ok",
    tone: "critical",
    fbs: 0,
    ...patch,
  };
}

describe("board", () => {
  it("words the days column for every status", () => {
    expect(daysText(row("1"))).toBe("5 дн.");
    expect(daysText(row("1", { status: "no_sales", days: null }))).toBe("нет продаж");
    expect(daysText(row("1", { status: "no_data", days: null }))).toBe("нет данных");
  });

  it("sorts the shortest cover first and keeps rows without days last", () => {
    const rows = [
      row("a", { days: 30 }),
      row("b", { days: null, status: "no_sales" }),
      row("c", { days: 2 }),
    ];
    expect(sortBoardRows(rows, "days", "asc").map((item) => item.barcode)).toEqual(["c", "a", "b"]);
    expect(sortBoardRows(rows, "days", "desc").map((item) => item.barcode)).toEqual(["a", "c", "b"]);
  });

  it("filters by cabinet and by any text of the row", () => {
    const rows = [row("111", { note: "включить FBS" }), row("222", { seller_id: "s2", name: "Дрель" })];
    expect(visibleBoardRows(rows, "s2", "")).toHaveLength(1);
    expect(visibleBoardRows(rows, "", "fbs")).toHaveLength(1);
    expect(visibleBoardRows(rows, "", "дрель")[0].barcode).toBe("222");
  });

  it("warns only about cabinets that have rows", () => {
    const board = {
      sellers: [
        { seller_id: "s1", name: "Один", stock_at: null, stock_stale: false, fbs_at: null, orders_without_barcode: 0 },
        { seller_id: "s2", name: "Два", stock_at: null, stock_stale: false, fbs_at: null, orders_without_barcode: 3 },
      ],
      rows: [row("1")],
    } as unknown as Board;
    const notices = boardNotices(board, "");
    expect(notices).toHaveLength(1);
    expect(notices[0]).toContain("Один");
  });

  it("tells what the upload did per cabinet", () => {
    const text = importSummary([
      { name: "Яночкина", seller_name: "Яночкина", connected_now: true, added: 2, existing: 0, unknown: 1 },
      { name: "Кто-то", seller_name: null, connected_now: false, added: 0, existing: 0, unknown: 0 },
    ]);
    expect(text).toContain("Яночкина: добавлено 2, уже были 0, кабинет подключён, 1 баркодов не найдено");
    expect(text).toContain("«Кто-то» — такого кабинета нет в реестре");
  });
});
