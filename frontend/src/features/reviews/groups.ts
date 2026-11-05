import { applyFilters, type ArticleFilters, hasActiveFilters } from "./filters";
import { movementOn } from "./movement";
import type { DailyReviewSnapshot, ProductReviewHistory } from "./types";

/** Основной товар и его болванки. Товар вне группы — группа из одного товара без участников. */
export interface ProductGroup {
  id: string;
  main: ProductReviewHistory;
  members: ProductReviewHistory[];
}

export function buildGroups(
  products: readonly ProductReviewHistory[],
): ProductGroup[] {
  const groups = new Map<string, ProductGroup>();
  const order: string[] = [];
  for (const product of products) {
    const id = product.group_id ?? `product:${product.id}`;
    let group = groups.get(id);
    if (!group) {
      group = { id, main: product, members: [] };
      groups.set(id, group);
      order.push(id);
    } else if (
      product.group_role === "main" &&
      group.main.group_role !== "main"
    ) {
      group.members.push(group.main);
      group.main = product;
    } else {
      group.members.push(product);
    }
  }
  return order.map((id) => groups.get(id) as ProductGroup);
}

export function isGroup(group: ProductGroup): boolean {
  return group.members.length > 0;
}

/** Счётчики, которые видны в свёрнутой строке: сумма по группе или сам товар. */
export function collapsedSnapshots(group: ProductGroup): DailyReviewSnapshot[] {
  if (!isGroup(group)) return group.main.snapshots;
  return group.main.group_snapshots.length
    ? group.main.group_snapshots
    : group.main.snapshots;
}

export interface GroupSelection {
  groups: ProductGroup[];
  /** Группы, раскрытые потому, что под фильтр попали только болванки. */
  revealed: Set<string>;
}

/** Группа видна, если под фильтр попал основной товар или любая болванка. */
export function filterGroups(
  groups: readonly ProductGroup[],
  filters: ArticleFilters,
): GroupSelection {
  if (!hasActiveFilters(filters))
    return { groups: [...groups], revealed: new Set() };
  const revealed = new Set<string>();
  const visible = groups.filter((group) => {
    if (applyFilters([group.main], filters).length) return true;
    if (!applyFilters(group.members, filters).length) return false;
    revealed.add(group.id);
    return true;
  });
  return { groups: visible, revealed };
}

/** Сначала товары с болванками, за ними одиночки; внутри — по движению отзывов за день. */
export function sortGroupsByMovement(
  groups: readonly ProductGroup[],
  date: string | null,
): ProductGroup[] {
  return groups
    .map((group, index) => ({
      group,
      index,
      delta: movementOn({ snapshots: collapsedSnapshots(group) }, date).delta,
    }))
    .sort((left, right) => {
      const grouped =
        Number(isGroup(right.group)) - Number(isGroup(left.group));
      if (grouped !== 0) return grouped;
      const moved = Math.abs(right.delta ?? 0) - Math.abs(left.delta ?? 0);
      return moved !== 0 ? moved : left.index - right.index;
    })
    .map((entry) => entry.group);
}

export function pluralizeDummies(count: number): string {
  const withinHundred = count % 100;
  const lastDigit = count % 10;
  if (withinHundred >= 11 && withinHundred <= 14) return "болванок";
  if (lastDigit === 1) return "болванка";
  if (lastDigit >= 2 && lastDigit <= 4) return "болванки";
  return "болванок";
}
