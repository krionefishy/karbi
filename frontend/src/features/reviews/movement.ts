import { totalReviews } from "./rating";
import type { DailyReviewSnapshot } from "./types";

export interface MovingProduct {
  snapshots: DailyReviewSnapshot[];
}

export interface Movement {
  /** Change in the total review count between the last two collected days. */
  delta: number | null;
  latest: number | null;
  previous: number | null;
}

export const NO_MOVEMENT: Movement = { delta: null, latest: null, previous: null };

function previousDay(date: string): string {
  const cursor = new Date(`${date}T00:00:00Z`);
  cursor.setUTCDate(cursor.getUTCDate() - 1);
  return cursor.toISOString().slice(0, 10);
}

/**
 * The most recent day anyone was collected on.
 *
 * Taken from the data rather than from the clock: a sync that failed, or one
 * that has not run yet today, would otherwise leave every product without a
 * comparison and flatten the ordering exactly when it matters.
 */
export function latestCollectedDate(products: readonly MovingProduct[]): string | null {
  let latest: string | null = null;
  for (const product of products) {
    for (const snapshot of product.snapshots) {
      if (latest === null || snapshot.date > latest) latest = snapshot.date;
    }
  }
  return latest;
}

/** Movement of one product between `date` and the day before it. */
export function movementOn(product: MovingProduct, date: string | null): Movement {
  if (date === null) return NO_MOVEMENT;
  const byDate = new Map(product.snapshots.map((snapshot) => [snapshot.date, snapshot]));
  const latest = byDate.get(date);
  const previous = byDate.get(previousDay(date));
  if (!latest) return NO_MOVEMENT;
  const latestTotal = totalReviews(latest.ratings);
  if (!previous) return { delta: null, latest: latestTotal, previous: null };
  const previousTotal = totalReviews(previous.ratings);
  return { delta: latestTotal - previousTotal, latest: latestTotal, previous: previousTotal };
}

/**
 * Products that moved first, by how much they moved regardless of direction —
 * a товар that lost thirteen reviews overnight deserves attention as much as
 * one that gained three. Everything else keeps the order it arrived in.
 */
export function sortByMovement<T extends MovingProduct>(products: readonly T[], date: string | null): T[] {
  return products
    .map((product, index) => ({ product, index, delta: movementOn(product, date).delta }))
    .sort((left, right) => {
      const moved = Math.abs(right.delta ?? 0) - Math.abs(left.delta ?? 0);
      return moved !== 0 ? moved : left.index - right.index;
    })
    .map((entry) => entry.product);
}
