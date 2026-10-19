import type { PodsortSellerState } from "./types";

const dayFormatter = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit" });

export function shortDay(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  return dayFormatter.format(new Date(year, month - 1, day));
}

export function sellerProblem(state: PodsortSellerState, windowDays: number): string | null {
  if (state.collection_error) return `заказы не догружаются: ${state.collection_error}`;
  if (state.window_days_loaded < windowDays) {
    return `заказы за ${state.window_days_loaded} из ${windowDays} дн. окна — расчёт занижен, догрузка идёт`;
  }
  if (!state.remains_at) {
    return state.remains_error
      ? `остатки по складам WB не собираются: ${state.remains_error}`
      : "остатки по складам WB ещё не собирались — остаток принят за 0";
  }
  if (state.remains_stale) {
    const reason = state.remains_error ? ` (${state.remains_error})` : "";
    return `остатки по складам WB давно не обновлялись${reason} — вычитается устаревший остаток`;
  }
  return null;
}

export function coverLabel(value: number | null): string {
  if (value === null) return "—";
  return value >= 100 ? "100+" : value.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
}
