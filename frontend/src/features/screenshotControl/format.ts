// Чистая логика страницы «Контроль скриншотов ДБС»: периоды, ярлыки, склонения, время.
// Вёрстка — в components/Screenshot*.tsx.

import type { Outcome, Status, Submission } from "./types";

export type PeriodKind = "day" | "week" | "month" | "custom";

export interface PeriodRange {
  from: string;
  to: string;
}

export const OUTCOMES: Outcome[] = ["positive", "amended", "deleted"];

const OUTCOME_LABELS: Record<Outcome, string> = { positive: "Полож", amended: "Доп", deleted: "Удален" };

export const outcomeLabel = (outcome: Outcome | null): string => (outcome ? OUTCOME_LABELS[outcome] : "без исхода");

const moscowDay = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Europe/Moscow",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** Сегодняшняя дата по Москве, YYYY-MM-DD: отчётные периоды бэкенд считает по МСК. */
export const moscowToday = (now: Date = new Date()): string => moscowDay.format(now);

function shiftDays(day: string, delta: number): string {
  const date = new Date(`${day}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + delta);
  return date.toISOString().slice(0, 10);
}

/** День — сегодня, неделя — с понедельника, месяц — с первого числа; конец всегда сегодня. */
export function periodRange(kind: Exclude<PeriodKind, "custom">, now: Date = new Date()): PeriodRange {
  const today = moscowToday(now);
  if (kind === "day") return { from: today, to: today };
  if (kind === "month") return { from: `${today.slice(0, 8)}01`, to: today };
  const weekday = new Date(`${today}T00:00:00Z`).getUTCDay();
  return { from: shiftDays(today, -((weekday + 6) % 7)), to: today };
}

/** Ошибка своего диапазона или пустая строка, если диапазон годится. */
export function customRangeError(range: PeriodRange): string {
  if (!range.from || !range.to) return "Укажите обе даты";
  if (range.from > range.to) return "Начало позже конца";
  return "";
}

/** plural(3, ["файл", "файла", "файлов"]) → «файла». */
export function plural(count: number, forms: [string, string, string]): string {
  const mod100 = Math.abs(count) % 100;
  const mod10 = mod100 % 10;
  if (mod100 >= 11 && mod100 <= 14) return forms[2];
  if (mod10 === 1) return forms[0];
  if (mod10 >= 2 && mod10 <= 4) return forms[1];
  return forms[2];
}

export type Tone = "accent" | "positive" | "warning" | "negative" | "neutral";

const STATUS: Record<Status, { label: string; tone: Tone }> = {
  processing: { label: "В обработке", tone: "accent" },
  awaiting_outcome: { label: "Нужен исход", tone: "warning" },
  accepted: { label: "Принят", tone: "positive" },
  needs_review: { label: "Проверяется", tone: "warning" },
  duplicate: { label: "Дубль", tone: "negative" },
  rejected: { label: "Не засчитан", tone: "neutral" },
};

export const statusLabel = (status: Status): string => STATUS[status].label;
export const statusTone = (status: Status): Tone => STATUS[status].tone;

/** Классы status-badge из ui.css по тону; нейтральный — базовый серый бейдж. */
export function toneBadgeClass(tone: Tone): string {
  const extra = {
    accent: "status-running",
    positive: "status-active",
    warning: "status-degraded",
    negative: "status-failed",
    neutral: "",
  }[tone];
  return extra ? `status-badge ${extra}` : "status-badge";
}

export const statusBadgeClass = (status: Status): string => toneBadgeClass(statusTone(status));

export const STATUS_FILTERS: Status[] = [
  "processing",
  "awaiting_outcome",
  "accepted",
  "needs_review",
  "duplicate",
  "rejected",
];

/**
 * Опрашивать список, пока конвейер что-то распознаёт. «Нужен исход» ждёт менеджера до суток —
 * опрос ему не поможет, а исход менеджер ставит сам и список обновляется после этого.
 */
export const hasPending = (items: Pick<Submission, "status">[]): boolean => items.some((item) => item.status === "processing");

const timeFormatter = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });
const dayFormatter = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit" });

const localDay = (date: Date) => `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`;

/** «14:32» для сегодняшнего, «22.10, 14:32» для прошлых дней. */
export function formatMoment(iso: string, now: Date = new Date()): string {
  const date = new Date(iso);
  const time = timeFormatter.format(date);
  return localDay(date) === localDay(now) ? time : `${dayFormatter.format(date)}, ${time}`;
}

/** Вторая строка в списке: причина, если она важна менеджеру, иначе хвост чата. */
export function submissionNote(
  submission: Submission,
  now: Date = new Date(),
): { text: string; reason: boolean } | null {
  if (submission.status === "duplicate") {
    return {
      text: submission.duplicate_received_at
        ? `уже загружен ${formatMoment(submission.duplicate_received_at, now)}`
        : submission.reason_text || "уже загружен ранее",
      reason: true,
    };
  }
  if (submission.status === "rejected" || submission.status === "awaiting_outcome") {
    const fallback = submission.status === "awaiting_outcome" ? "Выберите исход" : "Не засчитан";
    return { text: submission.reason_text || fallback, reason: true };
  }
  const tail = submission.fields?.chat_tail;
  return tail ? { text: `чат …${tail}`, reason: false } : null;
}

/** Обратный отсчёт «мм:сс»; ноль и меньше — «00:00». */
export function formatCountdown(msLeft: number): string {
  const total = Math.max(0, Math.ceil(msLeft / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

/** «2,4 МБ» для подсказок о размере файла. */
export function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} КБ`;
  const mb = bytes / (1024 * 1024);
  return `${mb.toLocaleString("ru-RU", { maximumFractionDigits: mb < 10 ? 1 : 0 })} МБ`;
}

/** Подпись пояса менеджера: «Москва, UTC+3»; пояса нет в списке — его id как есть. */
export function zoneLabel(settings: { timezone: string; zones: { id: string; label: string }[] }): string {
  return settings.zones.find((zone) => zone.id === settings.timezone)?.label ?? settings.timezone;
}
