import type { ArticleTurnover } from "./types";

export type CoverTone = "critical" | "warning" | "calm" | "neutral";

export interface CoverLabel {
  text: string;
  tone: CoverTone;
}

/**
 * How urgent one article is. Half the threshold is the point where a normal
 * delivery no longer arrives in time, so it reads differently from "soon".
 */
export function coverLabel(article: ArticleTurnover, threshold: number): CoverLabel {
  if (article.days_of_cover === null) return { text: "—", tone: "neutral" };
  const days = article.days_of_cover;
  if (days < threshold / 2) return { text: `${days.toFixed(1)} дн.`, tone: "critical" };
  if (days < threshold) return { text: `${days.toFixed(1)} дн.`, tone: "warning" };
  return { text: `${days.toFixed(1)} дн.`, tone: "calm" };
}

export function belowThreshold(articles: ArticleTurnover[], threshold: number): ArticleTurnover[] {
  return articles.filter((item) => item.days_of_cover !== null && item.days_of_cover < threshold);
}
