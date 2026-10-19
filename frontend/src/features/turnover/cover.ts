import type { ArticleTurnover } from "./types";

export type CoverTone = "critical" | "warning" | "calm" | "neutral";

export interface CoverLabel {
  text: string;
  tone: CoverTone;
}

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

export function isDormant(article: ArticleTurnover): boolean {
  return article.stock_total === 0 && article.orders_count === 0;
}
