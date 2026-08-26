import type { FbsRegion } from "./types";

/** Доли WB-направлений хранятся в сотых долях процента: 4000 — это 40%. */
export const BASIS_POINTS = 10_000;

export const toPercent = (shareBp: number) => shareBp / 100;

export const fromPercent = (percent: number) => Math.round(percent * 100);

export function sharesTotal(regions: Pick<FbsRegion, "share_bp">[]) {
  return regions.reduce((total, region) => total + region.share_bp, 0);
}

/**
 * Доли принимаются либо все нулевыми — логист их ещё не назвал, — либо ровно
 * в сумме сто процентов. Промежуточное состояние отдало бы складам меньше,
 * чем есть на самом деле.
 */
export function sharesAcceptable(regions: Pick<FbsRegion, "share_bp">[]) {
  const total = sharesTotal(regions);
  return total === 0 || total === BASIS_POINTS;
}
