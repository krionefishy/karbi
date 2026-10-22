import type { CardDocumentItem, CardDocuments, CardDocumentsSummary, DocumentState } from "./types";

export type DocumentFilter = "problems" | "all" | DocumentState;

export const PROBLEM_STATES: DocumentState[] = ["rejected", "missing", "expiring", "mismatch"];

const stateLabels: Record<DocumentState, string> = {
  rejected: "Отклонён WB",
  missing: "Нет документа",
  expiring: "Истекает срок",
  mismatch: "Расхождение",
  ok: "В порядке",
};

const reasonLabels: Record<string, string> = {
  valid: "документ действителен",
  document_not_found: "документ не найден в реестре",
  documents_invalid: "документ признан недействительным",
  documents_missing: "документ не загружен",
  expired: "срок действия истёк",
  number_mismatch: "номер в характеристиках не совпадает с документом",
  start_date_mismatch: "дата регистрации в характеристиках не совпадает с документом",
  end_date_mismatch: "дата окончания в характеристиках не совпадает с документом",
  status_2: "WB отклонил документ, причину не указал",
};

export function stateLabel(state: DocumentState): string {
  return stateLabels[state];
}

export function reasonLabel(reason: string | null): string {
  if (!reason) return "—";
  const label = reasonLabels[reason];
  return label ? label.charAt(0).toUpperCase() + label.slice(1) : reason;
}

export type Tone = "negative" | "warning" | "positive" | "neutral";

export function stateTone(state: DocumentState): Tone {
  if (state === "ok") return "positive";
  if (state === "rejected" || state === "missing") return "negative";
  return "warning";
}

export function daysLeftText(days: number | null): string {
  if (days === null) return "—";
  if (days < 0) return `истёк ${-days} дн. назад`;
  if (days === 0) return "истекает сегодня";
  return `осталось ${days} дн.`;
}

export function daysTone(days: number | null): Tone {
  if (days === null) return "neutral";
  if (days <= 7) return "negative";
  if (days <= 30) return "warning";
  return "neutral";
}

export function formatDay(value: string | null): string {
  if (!value) return "";
  const [year, month, day] = value.split("-");
  return year && month && day ? `${day}.${month}.${year}` : value;
}

export function documentPeriod(start: string | null, end: string | null): string {
  if (!start && !end) return "";
  if (!end) return `с ${formatDay(start)}, бессрочно`;
  if (!start) return `до ${formatDay(end)}`;
  return `${formatDay(start)} – ${formatDay(end)}`;
}

export interface Tile {
  key: DocumentFilter;
  label: string;
  count: number;
}

export function problemCount(summary: CardDocumentsSummary): number {
  return PROBLEM_STATES.reduce((sum, state) => sum + summary[state], 0);
}

export function tiles(summary: CardDocumentsSummary): Tile[] {
  return [
    { key: "problems", label: "С проблемами", count: problemCount(summary) },
    { key: "rejected", label: "Отклонены WB", count: summary.rejected },
    { key: "missing", label: "Нет документа", count: summary.missing },
    { key: "expiring", label: "Истекает срок", count: summary.expiring },
    { key: "mismatch", label: "Расхождения", count: summary.mismatch },
    { key: "ok", label: "В порядке", count: summary.ok },
    { key: "all", label: "Всего карточек", count: summary.total },
  ];
}

export function visibleItems(items: CardDocumentItem[], filter: DocumentFilter, search: string): CardDocumentItem[] {
  const query = search.trim().toLowerCase();
  return items.filter((item) => {
    if (filter === "problems" && item.state === "ok") return false;
    if (filter !== "problems" && filter !== "all" && item.state !== filter) return false;
    if (!query) return true;
    return [String(item.nm_id), item.vendor_code, item.title, item.document_number ?? ""].some((value) =>
      value.toLowerCase().includes(query),
    );
  });
}

export interface Notice {
  tone: "error" | "warning";
  title: string;
  text: string;
}

export function notices(data: CardDocuments, formatMoment: (iso: string) => string): Notice[] {
  const collected: Notice[] = [];
  if (data.stale) {
    collected.push({
      tone: "warning",
      title: "Наблюдение не работает",
      text: data.collected_at
        ? `Последний успешный сбор — ${formatMoment(data.collected_at)}. Новые проблемы с документами сейчас не видны: это не «ошибок нет», а «не знаем».`
        : "Успешных сборов по кабинету ещё не было. Новые проблемы с документами сейчас не видны: это не «ошибок нет», а «не знаем».",
    });
  }
  if (data.last_error) {
    collected.push({
      tone: "error",
      title: "Последний сбор не удался",
      text: `${data.last_error}. Показаны состояния с прошлого успешного сбора.`,
    });
  }
  return collected;
}

export function emptyMessage(filter: DocumentFilter, summary: CardDocumentsSummary, search: string): string {
  if (search.trim()) return "По запросу карточек не нашлось.";
  if (summary.total === 0) return "В кабинете нет карточек с документами.";
  if (filter === "problems") return `Проблем нет: у всех ${summary.total} карточек документы в порядке.`;
  return "В этой группе карточек нет.";
}
