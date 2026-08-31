import type { ArticleTurnover } from "./types";

export type CoverTone = "critical" | "warning" | "calm" | "neutral";

export interface CoverLabel {
  text: string;
  tone: CoverTone;
}

/**
 * How urgent one article is. Half the threshold is the point where a normal
 * delivery no longer arrives in time, so it reads differently from "soon".
 *
 * Дни приходят целыми: доля дня — не день, который у продавца есть.
 */
export function coverLabel(article: ArticleTurnover, threshold: number): CoverLabel {
  if (article.days_of_cover === null) return { text: "—", tone: "neutral" };
  const days = article.days_of_cover;
  if (days < threshold / 2) return { text: `${days} дн.`, tone: "critical" };
  if (days < threshold) return { text: `${days} дн.`, tone: "warning" };
  return { text: `${days} дн.`, tone: "calm" };
}

export function belowThreshold(articles: ArticleTurnover[], threshold: number): ArticleTurnover[] {
  return articles.filter((item) => item.days_of_cover !== null && item.days_of_cover < threshold);
}

/**
 * A товар with nothing on the shelf and nothing ordered inside the window is
 * the assortment tail. It stays in the data — «не считали» and «нечего
 * считать» must remain distinguishable — but it has no business filling the
 * table.
 */
export function isDormant(article: ArticleTurnover): boolean {
  return article.stock_total === 0 && article.orders_count === 0;
}
