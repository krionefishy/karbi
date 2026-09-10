import type { Checklist, ChecklistItem, ChecklistRow, ItemState } from "./types";

export interface RowFilter {
  search: string;
  onlyPending: boolean;
}

export function visibleRows(rows: ChecklistRow[], filter: RowFilter): ChecklistRow[] {
  const query = filter.search.trim().toLowerCase();
  return rows.filter((row) => {
    if (filter.onlyPending && row.ready) return false;
    if (!query) return true;
    return [row.article, row.vendor_code, row.title, row.barcode].some((value) => value.toLowerCase().includes(query));
  });
}

export type CellTone = "done" | "missing" | "neutral";

/** Зелёный — есть, красный — нет, серый — данных нет или пункт справочный. */
export function cellTone(item: ChecklistItem, state: ItemState): CellTone {
  if (!item.counted || state.done === null) return "neutral";
  return state.done ? "done" : "missing";
}

export function cellHint(item: ChecklistItem, state: ItemState): string {
  const lines = [item.meaning];
  if (state.detail) lines.push(`WB: ${state.detail}`);
  if (state.note) lines.push(state.note);
  if (item.counted && state.done === null) lines.push("Данных пока нет.");
  return lines.join("\n");
}

/** Why the table is empty or some cells are blank — said once above the table, not in every cell. */
export function notices(data: Checklist): string[] {
  const collected: string[] = [];
  if (data.stock_state === "not_connected") {
    collected.push(
      "Товары отбираются по остатку, а остатки собирает «Оборачиваемость» — селлер к ней не подключён, поэтому таблица пуста.",
    );
  }
  if (data.stock_state === "stale") {
    collected.push("Свежих снимков остатков нет больше двух суток: «Оборачиваемость» не собирает данные.");
  }
  if (data.reviews_state === "not_connected") {
    collected.push("Пункты про отзывы без данных: селлер не подключён к «Мониторингу отзывов».");
  }
  if (data.reviews_state === "no_snapshot") {
    collected.push("Пункты про отзывы заполнятся после первого ночного прогона отзывов.");
  }
  return collected;
}
