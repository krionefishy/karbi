import { describe, expect, it } from "vitest";

import { gridTemplate, notices, rowCells, visibleRows } from "./board";
import type { StocksBoard, StocksGroup, StocksRow } from "./types";

const own: StocksGroup = {
  id: "g-own",
  title: "Наш склад",
  kind: "own",
  columns: [
    { warehouse_id: 1, name: "4. Питер наш" },
    { warehouse_id: 2, name: "5. Казань наша" },
  ],
};
const northwest: StocksGroup = {
  id: "g-nw",
  title: "Северо-западный округ",
  kind: "district",
  columns: [{ warehouse_id: 3, name: "Фулэксперт СПБ" }],
};

function row(overrides: Partial<StocksRow> = {}): StocksRow {
  return {
    barcode: "2052957817499",
    note: "",
    article: "1223328520",
    title: "Фен для волос",
    vendor_code: "Фен",
    in_catalog: true,
    amounts: { "1": 0, "2": 5, "3": 28 },
    totals: { "g-own": 5, "g-nw": 28 },
    ...overrides,
  };
}

function board(overrides: Partial<StocksBoard> = {}): StocksBoard {
  return {
    seller_id: "s",
    seller_name: "ИП",
    collected_at: null,
    collection_error: null,
    groups: [own, northwest],
    rows: [row()],
    ...overrides,
  };
}

describe("rowCells", () => {
  it("раскрытая группа даёт сводный столбец и склады, свёрнутая — только сводный", () => {
    const open = rowCells(row(), [own, northwest], new Set());
    expect(open.map((cell) => cell.value)).toEqual([5, 0, 5, 28, 28]);

    const folded = rowCells(row(), [own, northwest], new Set(["g-own"]));
    expect(folded.map((cell) => (cell.kind === "group" ? `Σ${cell.value}` : cell.value))).toEqual(["Σ5", "Σ28", 28]);
  });

  it("склад без строки остатка — ноль, а не пустота", () => {
    const cells = rowCells(row({ amounts: {}, totals: {} }), [own], new Set());
    expect(cells.map((cell) => cell.value)).toEqual([0, 0, 0]);
  });
});

describe("gridTemplate", () => {
  it("считает столбцы по раскрытым группам", () => {
    expect(gridTemplate([own, northwest], new Set(["g-nw"])).split(" ")).toHaveLength(3 + 3 + 1 + 1);
  });
});

describe("visibleRows", () => {
  it("ищет по баркоду, артикулу, названию и заметке", () => {
    const rows = [row(), row({ barcode: "2000000000001", article: "999", title: "Щётка", note: "дубль карточки" })];
    expect(visibleRows(rows, "дубль").map((item) => item.barcode)).toEqual(["2000000000001"]);
    expect(visibleRows(rows, "12233")).toHaveLength(1);
    expect(visibleRows(rows, "")).toHaveLength(2);
  });
});

describe("notices", () => {
  it("объясняет пустую таблицу и баркоды вне каталога", () => {
    expect(notices(board({ groups: [], rows: [] }))).toHaveLength(2);
    expect(notices(board({ rows: [row({ in_catalog: false })] }))[0]).toMatch(/не найдена в каталоге/);
    expect(notices(board())).toEqual([]);
  });
});
