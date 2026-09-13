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

/** Зелёный — выполнено, красный — нет, серый — данных нет. */
export function cellTone(state: ItemState): CellTone {
  if (state.done === null) return "neutral";
  return state.done ? "done" : "missing";
}

export function cellHint(item: ChecklistItem, state: ItemState): string {
  const lines = [item.meaning];
  if (state.detail) lines.push(`WB: ${state.detail}`);
  if (state.note) lines.push(state.note);
  if (state.done === null) lines.push("Данных пока нет.");
  return lines.join("\n");
}

/** Why the table is empty or some cells are blank — said once above the table, not in every cell. */
export function notices(data: Checklist): string[] {
  const collected: string[] = [];
  if (data.stock_state === "not_connected") {
    // Остатки собирает общее зеркало WB по всем селлерам; до селлера оно ещё
    // не дошло — так бывает первые минуты после подключения.
    collected.push("Товары отбираются по остатку, а остатки этого селлера ещё не собраны — таблица заполнится в ближайший час.");
  }
  if (data.stock_state === "stale") {
    collected.push("Остатки не обновлялись больше двух суток: сбор данных WB стоит, таблица показана по старым числам.");
  }
  if (data.reviews_state === "not_connected") {
    collected.push("Пункты про отзывы без данных: отзывы этого селлера ещё не собраны, заполнятся после ближайшего сбора.");
  }
  if (data.reviews_state === "no_snapshot") {
    collected.push("Пункты про отзывы без данных: ни у одной карточки отзывов пока нет.");
  }
  return collected;
}
