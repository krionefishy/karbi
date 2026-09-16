/** Период по умолчанию — последняя неделя по дате операции отчёта, в местном календаре: отчёт WB недельный. */
export function defaultPeriod(today = new Date()): { dateFrom: string; dateTo: string } {
  const to = new Date(today);
  const from = new Date(today);
  from.setDate(from.getDate() - 7);
  return { dateFrom: isoDay(from), dateTo: isoDay(to) };
}

export function isoDay(value: Date): string {
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${value.getFullYear()}-${month}-${day}`;
}

/** «2026-09-01/2026-09-07» → «01.09 — 07.09.2026». */
export function periodLabel(period: string): string {
  const [from, to] = period.split("/");
  if (!from || !to) return period;
  return `${short(from)} — ${short(to)}${to.slice(0, 4) ? `.${to.slice(0, 4)}` : ""}`;
}

function short(iso: string): string {
  const [, month, day] = iso.split("-");
  return `${day}.${month}`;
}

const moneyFormatter = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function money(value: number): string {
  return `${moneyFormatter.format(value)} ₽`;
}
