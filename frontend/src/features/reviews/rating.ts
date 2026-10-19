import type { RatingCounts } from "./types";

export const RATING_TARGET = 4.95;

const RATINGS = [1, 2, 3, 4, 5] as const;

const ROUNDING_SLACK = 1e-9;

export function totalReviews(counts: RatingCounts): number {
  return RATINGS.reduce((total, rating) => total + counts[rating], 0);
}

export function averageRating(counts: RatingCounts): number | null {
  const total = totalReviews(counts);
  if (!total) return null;
  return RATINGS.reduce((sum, rating) => sum + rating * counts[rating], 0) / total;
}

export function ratingDebt(counts: RatingCounts): number {
  return RATINGS.reduce((debt, rating) => debt + (5 - rating) * counts[rating], 0);
}

export function fiveStarsToTarget(counts: RatingCounts, target = RATING_TARGET): number {
  const total = totalReviews(counts);
  if (!total) return 0;
  const needed = ratingDebt(counts) / (5 - target) - total;
  return Math.max(0, Math.ceil(needed - ROUNDING_SLACK));
}

export function pluralizeFives(count: number): string {
  const withinHundred = count % 100;
  const lastDigit = count % 10;
  if (withinHundred >= 11 && withinHundred <= 14) return "пятёрок";
  if (lastDigit === 1) return "пятёрка";
  if (lastDigit >= 2 && lastDigit <= 4) return "пятёрки";
  return "пятёрок";
}
