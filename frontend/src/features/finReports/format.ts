import type { CostUploadResult, PnlPeriod } from "./types";

const moneyFormatter = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function money(value: number | undefined): string {
  if (!value) return "—";
  const text = moneyFormatter.format(value);
  return /^-?0,00$/.test(text) ? "—" : text;
}

export function defaultPeriod(periods: PnlPeriod[]): string {
  return (periods.find((item) => !item.open && item.pending_sellers.length === 0) ?? periods[0])?.key ?? "";
}

export function localToday(now: Date = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function plural(count: number, one: string, few: string, many: string): string {
  const tail = count % 100;
  if (tail >= 11 && tail <= 14) return many;
  if (count % 10 === 1) return one;
  if (count % 10 >= 2 && count % 10 <= 4) return few;
  return many;
}

export const articles = (count: number) => `${count} ${plural(count, "артикул", "артикула", "артикулов")}`;
export const reports = (count: number) => `${count} ${plural(count, "отчёт", "отчёта", "отчётов")}`;
export const days = (count: number) => `${count} ${plural(count, "день", "дня", "дней")}`;

export function uploadSummary(result: CostUploadResult): string {
  const marketplace = result.marketplace === "wb" ? "Wildberries" : "Ozon";
  const parts = [`новых — ${result.added}`, `цена изменилась — ${result.changed}`, `без изменений — ${result.unchanged}`];
  return `${marketplace}: ${parts.join(", ")}.`;
}
