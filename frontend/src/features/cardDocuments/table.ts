import type { CardDocumentItem, CardDocuments, CardDocumentsSummary, DocumentState, Notice, NoticeLevel } from "./types";

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

export interface Banner {
  key: string;
  level: NoticeLevel;
  title: string;
  text: string | null;
}

const mskFormatter = new Intl.DateTimeFormat("ru-RU", {
  timeZone: "Europe/Moscow",
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export function formatMsk(iso: string): string {
  return `${mskFormatter.format(new Date(iso))} МСК`;
}

export function agoText(iso: string, now: Date): string {
  const minutes = Math.max(0, Math.round((now.getTime() - new Date(iso).getTime()) / 60_000));
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} ч назад`;
  return `${Math.floor(hours / 24)} дн. назад`;
}

function banner(notice: Notice, data: CardDocuments, canInvite: boolean, now: Date): Omit<Banner, "key" | "level"> {
  switch (notice.kind) {
    case "stale": {
      const last = notice.since ?? data.collected_at;
      return {
        title: "Наблюдение не работает",
        text: last
          ? `Последний сбор был ${agoText(last, now)} (${formatMsk(last)}). Состояния ниже могли устареть.`
          : "Успешных сборов по кабинету ещё не было. Состояния ниже могли устареть.",
      };
    }
    case "collection_error":
      return { title: "Последний сбор не удался, показаны прежние состояния", text: notice.detail };
    case "permanent_error":
      return {
        title: "Кабинет не читается",
        text: notice.detail ? `${notice.detail}. Нужно вмешательство.` : "Нужно вмешательство.",
      };
    case "no_subscribers":
      return {
        title: "Некому слать уведомления: на кабинет никто не подписан",
        text: canInvite ? "Нажмите «Ссылка на бота» и отправьте ссылку сотруднику." : null,
      };
    case "bot_missing":
      return { title: "Бот уведомлений не зарегистрирован — обратитесь к администратору", text: null };
    default:
      return { title: notice.detail ?? notice.kind, text: null };
  }
}

export function banners(data: CardDocuments, canInvite: boolean, now: Date = new Date()): Banner[] {
  return (data.notices ?? []).map((notice, index) => ({
    key: `${notice.kind}:${index}`,
    level: notice.level === "danger" ? "danger" : "warning",
    ...banner(notice, data, canInvite, now),
  }));
}

export function emptyMessage(filter: DocumentFilter, summary: CardDocumentsSummary, search: string): string {
  if (search.trim()) return "По запросу карточек не нашлось.";
  if (summary.total === 0) return "В кабинете нет карточек с документами.";
  if (filter === "problems") return `Проблем нет: у всех ${summary.total} карточек документы в порядке.`;
  return "В этой группе карточек нет.";
}
