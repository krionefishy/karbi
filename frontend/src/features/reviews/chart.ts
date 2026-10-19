import { daysEndingAt, previousDay } from "./days";
import { totalReviews } from "./rating";
import type { DailyReviewSnapshot } from "./types";

export const CHART_DAYS = 30;

export interface ChartPoint {
  date: string;
  total: number | null;
  delta: number | null;
}

export interface Scale {
  floor: number;
  ceiling: number;
}

export type Run = Array<ChartPoint & { total: number; index: number }>;

export function buildSeries(
  snapshots: readonly DailyReviewSnapshot[],
  endDate: string,
  days: number = CHART_DAYS,
): ChartPoint[] {
  const totals = new Map(snapshots.map((snapshot) => [snapshot.date, totalReviews(snapshot.ratings)]));
  return daysEndingAt(endDate, days).map((date) => {
    const total = totals.get(date);
    if (total === undefined) return { date, total: null, delta: null };
    const before = totals.get(previousDay(date));
    return { date, total, delta: before === undefined ? null : total - before };
  });
}

export function verticalScale(points: readonly ChartPoint[]): Scale {
  const totals = points.map((point) => point.total).filter((total): total is number => total !== null);
  if (totals.length === 0) return { floor: 0, ceiling: 1 };
  const low = Math.min(...totals);
  const high = Math.max(...totals);
  const padding = Math.max(1, Math.round((high - low) * 0.18));
  return { floor: low - padding, ceiling: high + padding };
}

export function ticksFor(scale: Scale, count = 4): number[] {
  const span = scale.ceiling - scale.floor;
  const seen = new Set<number>();
  for (let step = 0; step < count; step += 1) {
    seen.add(Math.round(scale.floor + (span / (count - 1)) * step));
  }
  return [...seen];
}

export function runsOf(points: readonly ChartPoint[]): Run[] {
  const runs: Run[] = [];
  let current: Run = [];
  points.forEach((point, index) => {
    if (point.total === null) {
      if (current.length) runs.push(current);
      current = [];
      return;
    }
    current.push({ ...point, total: point.total, index });
  });
  if (current.length) runs.push(current);
  return runs;
}

export interface ChartSummary {
  latestTotal: number | null;
  change: number | null;
  movedDays: number;
  missingDays: number;
}

export function summarize(points: readonly ChartPoint[]): ChartSummary {
  const known = points.filter((point) => point.total !== null);
  const first = known[0]?.total ?? null;
  const last = known[known.length - 1]?.total ?? null;
  return {
    latestTotal: last,
    change: first === null || last === null ? null : last - first,
    movedDays: points.filter((point) => point.delta).length,
    missingDays: points.length - known.length,
  };
}

export function formatSigned(value: number): string {
  if (value > 0) return `+${value.toLocaleString("ru-RU")}`;
  if (value < 0) return `−${Math.abs(value).toLocaleString("ru-RU")}`;
  return "0";
}
