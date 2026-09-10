import type { Checklist, ChecklistItem, ChecklistRow, ItemKind, ItemState } from "./types";

/** The table as it will look once the server accepts a tick — shown before it answers. */
export function applyMark(data: Checklist, article: string, key: string, checked: boolean): Checklist {
  return {
    ...data,
    rows: data.rows.map((row) => {
      if (row.article !== article) return row;
      const items = row.items.map((item) =>
        // Снятая отметка забирает с собой и предупреждение «факт пропал».
        item.key === key ? { ...item, checked, warning: checked ? item.warning : null } : item,
      );
      const done = items.filter((item) => item.checked).length;
      return { ...row, items, done, ready: done === items.length };
    }),
  };
}

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

const kindLabels: Record<ItemKind, string> = {
  auto: "WB",
  confirm: "WB + вы",
  manual: "вручную",
};

export const kindLabel = (kind: ItemKind) => kindLabels[kind];

/** A tick can be taken off by hand unless WB decides it; it can be put on only with a fact behind it. */
export function isLocked(state: ItemState): boolean {
  return state.kind === "auto" || (!state.checked && !state.can_check);
}

export function cellHint(item: ChecklistItem, state: ItemState): string {
  const lines = [item.meaning];
  if (state.detail) lines.push(`WB: ${state.detail}`);
  if (state.note) lines.push(state.note);
  if (state.warning) lines.push(state.warning);
  if (state.kind === "auto") {
    lines.push("Отмечается автоматически по данным WB.");
  } else if (state.kind === "confirm" && isLocked(state)) {
    lines.push(state.unknown ? "Данных пока нет." : "В данных WB этого нет — отмечать пока не на чем.");
  }
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
