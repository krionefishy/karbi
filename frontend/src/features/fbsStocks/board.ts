import type { StocksBoard, StocksGroup, StocksRow } from "./types";

export type BoardCell =
  | { kind: "group"; group: StocksGroup; value: number }
  | { kind: "warehouse"; group: StocksGroup; warehouseId: number; value: number };

export function rowCells(row: StocksRow, groups: StocksGroup[], collapsed: Set<string>): BoardCell[] {
  const cells: BoardCell[] = [];
  for (const group of groups) {
    cells.push({ kind: "group", group, value: row.totals[group.id] ?? 0 });
    if (collapsed.has(group.id)) continue;
    for (const column of group.columns) {
      cells.push({ kind: "warehouse", group, warehouseId: column.warehouse_id, value: row.amounts[String(column.warehouse_id)] ?? 0 });
    }
  }
  return cells;
}

export function gridTemplate(groups: StocksGroup[], collapsed: Set<string>): string {
  const data = groups.flatMap((group) => [
    "112px",
    ...(collapsed.has(group.id) ? [] : group.columns.map(() => "96px")),
  ]);
  return ["200px", "150px", "220px", ...data, "40px"].join(" ");
}

export function visibleRows(rows: StocksRow[], search: string): StocksRow[] {
  const query = search.trim().toLowerCase();
  if (!query) return rows;
  return rows.filter((row) =>
    [row.barcode, row.article, row.title, row.vendor_code, row.note].some((value) => value.toLowerCase().includes(query)),
  );
}

export function notices(board: StocksBoard): string[] {
  const collected: string[] = [];
  if (board.groups.length === 0) {
    collected.push("Столбцы ещё не настроены: откройте «Столбцы» и разложите склады кабинета по группам.");
  } else if (board.groups.every((group) => group.columns.length === 0)) {
    collected.push("В группах нет ни одного склада: остатки спрашивать неоткуда.");
  }
  if (board.rows.length === 0) {
    collected.push("Впишите баркоды — по ним остатки подтянутся при ближайшем обновлении.");
  }
  if (board.rows.some((row) => !row.in_catalog)) {
    collected.push("Часть баркодов не найдена в каталоге кабинета: остаток по ним спрошен, но название подсказать нечем.");
  }
  return collected;
}
