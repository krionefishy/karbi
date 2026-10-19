import { isoDay } from "../fbsPenalties/period";
import type { GroupSummary } from "./types";

export function defaultPeriod(today = new Date()): { dateFrom: string; dateTo: string } {
  const from = new Date(today);
  from.setDate(from.getDate() - 13);
  return { dateFrom: isoDay(from), dateTo: isoDay(today) };
}

const percentFormatter = new Intl.NumberFormat("ru-RU", { style: "percent", maximumFractionDigits: 1 });

export function percent(value: number | null): string {
  return value === null ? "—" : percentFormatter.format(value);
}

export function share(count: number, summary: GroupSummary): string {
  return summary.total ? `${count} из ${summary.total}` : "—";
}

export function rateShift(after: GroupSummary, before: GroupSummary): number | null {
  if (after.reply_rate === null || before.reply_rate === null) return null;
  return Math.round((after.reply_rate - before.reply_rate) * 1000) / 10;
}

export function shiftLabel(shift: number | null): string {
  if (shift === null) return "—";
  return `${shift > 0 ? "+" : ""}${shift.toLocaleString("ru-RU")} п.п.`;
}
