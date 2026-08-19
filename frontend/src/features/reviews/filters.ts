/**
 * Filtering for the seller's goods, shared by the timeline and the catalog
 * fallback. Both lists carry the same identifying fields, so the helpers work
 * off this minimum rather than either full type.
 */
export interface FilterableArticle {
  article: string;
  vendor_code: string;
  subject_name: string;
}

export interface Subject {
  name: string;
  count: number;
}

export interface ArticleFilters {
  query: string;
  subjects: string[];
}

/** Goods that never came from the catalog have no subject at all. */
export const NO_SUBJECT = "Без предмета";

export const EMPTY_FILTERS: ArticleFilters = { query: "", subjects: [] };

function normalize(value: string): string {
  return value.trim().replace(/\s+/g, " ").toLowerCase();
}

export function subjectOf(item: FilterableArticle): string {
  return item.subject_name.trim() || NO_SUBJECT;
}

/**
 * Matches the WB article, the seller's own article and the subject. A partial
 * number is enough — "5268" finds 1272526845 — because sellers paste fragments
 * out of spreadsheets.
 */
export function matchesQuery(item: FilterableArticle, query: string): boolean {
  const needle = normalize(query);
  if (!needle) return true;
  return [item.article, item.vendor_code, subjectOf(item)].some((field) => normalize(field).includes(needle));
}

export function matchesSubjects(item: FilterableArticle, subjects: string[]): boolean {
  return subjects.length === 0 || subjects.includes(subjectOf(item));
}

/** Subjects present in the list, most populated first, then alphabetically. */
export function collectSubjects(items: readonly FilterableArticle[]): Subject[] {
  const counts = new Map<string, number>();
  for (const item of items) {
    const subject = subjectOf(item);
    counts.set(subject, (counts.get(subject) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((left, right) => right.count - left.count || left.name.localeCompare(right.name, "ru"));
}

export function applyFilters<T extends FilterableArticle>(items: readonly T[], filters: ArticleFilters): T[] {
  return items.filter((item) => matchesSubjects(item, filters.subjects) && matchesQuery(item, filters.query));
}

export function hasActiveFilters(filters: ArticleFilters): boolean {
  return Boolean(filters.query.trim()) || filters.subjects.length > 0;
}
