import type { RatingCounts } from "./types";

/**
 * WB shows the rating to one decimal, so anything from 4.95 up already renders
 * as 5.0. A true 5.0 is unreachable while a single review below five exists —
 * the average approaches it but never arrives — so the displayed threshold is
 * what the goal has to be measured against.
 */
export const RATING_TARGET = 4.95;

const RATINGS = [1, 2, 3, 4, 5] as const;

/**
 * 1 / (5 - 4.95) has no exact binary form, so the quotient lands a hair above
 * the true integer and a bare ceil() would ask for one review too many.
 */
const ROUNDING_SLACK = 1e-9;

export function totalReviews(counts: RatingCounts): number {
  return RATINGS.reduce((total, rating) => total + counts[rating], 0);
}

export function averageRating(counts: RatingCounts): number | null {
  const total = totalReviews(counts);
  if (!total) return null;
  return RATINGS.reduce((sum, rating) => sum + rating * counts[rating], 0) / total;
}

/**
 * How far the reviews collectively fall short of a perfect score: a one-star
 * review costs four points, a four-star review costs one, fives cost nothing.
 */
export function ratingDebt(counts: RatingCounts): number {
  return RATINGS.reduce((debt, rating) => debt + (5 - rating) * counts[rating], 0);
}

/** Five-star reviews still needed for the average to reach `target`. */
export function fiveStarsToTarget(counts: RatingCounts, target = RATING_TARGET): number {
  const total = totalReviews(counts);
  if (!total) return 0;
  const needed = ratingDebt(counts) / (5 - target) - total;
  return Math.max(0, Math.ceil(needed - ROUNDING_SLACK));
}

/** Agreement for "ещё N пятёрок" — 1 пятёрка, 2 пятёрки, 5 пятёрок. */
export function pluralizeFives(count: number): string {
  const withinHundred = count % 100;
  const lastDigit = count % 10;
  if (withinHundred >= 11 && withinHundred <= 14) return "пятёрок";
  if (lastDigit === 1) return "пятёрка";
  if (lastDigit >= 2 && lastDigit <= 4) return "пятёрки";
  return "пятёрок";
}
