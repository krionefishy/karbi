import { isoDay } from "../fbsPenalties/period";
import type { GroupSummary } from "./types";

/** Две недели: рассылку включают и меняют текст днями, а не часами. */
export function defaultPeriod(today = new Date()): { dateFrom: string; dateTo: string } {
  const from = new Date(today);
  from.setDate(from.getDate() - 13);
  return { dateFrom: isoDay(from), dateTo: isoDay(today) };
}

const percentFormatter = new Intl.NumberFormat("ru-RU", { style: "percent", maximumFractionDigits: 1 });

export function percent(value: number | null): string {
  return value === null ? "—" : percentFormatter.format(value);
}

/** «12 из 40» — процент без абсолютных чисел на двадцати диалогах в день обманывает. */
export function share(count: number, summary: GroupSummary): string {
  const decided = summary.replied + summary.silent;
  return decided ? `${count} из ${decided}` : "—";
}

/** На сколько процентных пунктов с нашим сообщением отвечают реже (минус) или чаще (плюс), чем без него. */
export function rateShift(followed: GroupSummary, bare: GroupSummary): number | null {
  if (followed.reply_rate === null || bare.reply_rate === null) return null;
  return Math.round((followed.reply_rate - bare.reply_rate) * 1000) / 10;
}
