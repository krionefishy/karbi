// Чистая логика админки «Контроль скриншотов ДБС»: бейджи, решения, клавиши, сравнение пары,
// сигналы по менеджерам, CSV, предупреждения конвейера. Вёрстка — в components/ScreenshotAdmin*.tsx.

import { formatBytes, moscowToday, periodRange, type PeriodRange, type Tone } from "./format";
import type {
  AdminSubmission,
  ManagerStats,
  MatchKind,
  Mode,
  Outcome,
  Pipeline,
  QueueKind,
  ResolveDecision,
  ResolveRequest,
  Suspicion,
  Verdict,
} from "./types";

export type QueueTab = Exclude<QueueKind, "all">;

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

export interface Badge {
  label: string;
  tone: Tone;
}

const SUSPICION: Record<Suspicion, Badge> = {
  high: { label: "Высокий", tone: "negative" },
  medium: { label: "Средний", tone: "warning" },
  low: { label: "Низкий", tone: "neutral" },
};

export const suspicionBadge = (suspicion: Suspicion | null): Badge | null => (suspicion ? SUSPICION[suspicion] : null);

const VERDICT: Record<Verdict, Badge> = {
  accepted: { label: "Принят", tone: "positive" },
  needs_review: { label: "Проверяется", tone: "warning" },
  duplicate: { label: "Дубль", tone: "negative" },
  rejected: { label: "Не засчитан", tone: "neutral" },
};

export const verdictBadge = (verdict: Verdict | null): Badge =>
  verdict ? VERDICT[verdict] : { label: "В обработке", tone: "accent" };

const OUTCOME: Record<Outcome, string> = { positive: "Полож", amended: "Доп", deleted: "Удален" };
export const outcomeText = (outcome: Outcome | null): string => (outcome ? OUTCOME[outcome] : "без подписи");

const MODE: Record<Mode, Badge> = {
  off: { label: "Выключен", tone: "neutral" },
  shadow: { label: "Пробный", tone: "accent" },
  enforce: { label: "Боевой", tone: "positive" },
};
export const modeBadge = (mode: Mode): Badge => MODE[mode];

// --- решения ---

export type Action = "approve" | "decline" | "duplicate";

/** «Засчитать» значит разное: подтвердить аудит, снять подозрение в дубле или одобрить ревью. */
export function approveDecision(item: Pick<AdminSubmission, "verdict" | "audit_requested">): ResolveDecision {
  if (item.verdict === "accepted") return "audit_ok";
  if (item.verdict === "duplicate") return "not_duplicate";
  return "approve";
}

/** Комментарий обязателен при отклонении и когда снимаем уже засчитанный скрин. */
export function noteRequired(action: Action, item: Pick<AdminSubmission, "verdict">): boolean {
  if (action === "decline") return true;
  return action === "duplicate" && item.verdict === "accepted";
}

export function buildResolve(
  item: Pick<AdminSubmission, "verdict" | "audit_requested">,
  action: Action,
  options: { note?: string; target?: string | null } = {},
): ResolveRequest {
  const note = (options.note ?? "").trim();
  if (action === "approve") return { decision: approveDecision(item), note };
  if (action === "decline") return { decision: "decline", note };
  return { decision: "duplicate_of", duplicate_of: options.target ?? null, note };
}

// --- клавиши ---

export type KeyAction = Action | "next" | "prev";

export interface KeyLike {
  code: string;
  key: string;
  altKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  target?: EventTarget | null;
}

function isTyping(target: EventTarget | null | undefined): boolean {
  if (!target || typeof (target as HTMLElement).tagName !== "string") return false;
  const element = target as HTMLElement;
  return ["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName) || element.isContentEditable;
}

/**
 * Клавиши разбора очереди. Буквы — по физической клавише (event.code), чтобы работали и в русской
 * раскладке: KeyA = «ф», KeyD = «в», KeyN = «т». Кириллическая «Н» тоже ведёт к дублю — её рисует макет.
 */
export function keyAction(event: KeyLike): KeyAction | null {
  if (event.altKey || event.ctrlKey || event.metaKey || isTyping(event.target)) return null;
  switch (event.code) {
    case "KeyA":
      return "approve";
    case "KeyD":
      return "decline";
    case "KeyN":
      return "duplicate";
    case "ArrowRight":
    case "ArrowDown":
      return "next";
    case "ArrowLeft":
    case "ArrowUp":
      return "prev";
  }
  if (event.key === "н" || event.key === "Н") return "duplicate";
  return null;
}

/** Куда перейти после решения: на место ушедшей строки, у конца списка — на предыдущую. */
export function selectAfterRemoval(ids: string[], removed: string): string | null {
  const index = ids.indexOf(removed);
  const rest = ids.filter((id) => id !== removed);
  if (rest.length === 0) return null;
  if (index < 0) return rest[0];
  return rest[Math.min(index, rest.length - 1)];
}

export function stepSelection(ids: string[], current: string | null, delta: 1 | -1): string | null {
  if (ids.length === 0) return null;
  const index = current ? ids.indexOf(current) : -1;
  if (index < 0) return ids[0];
  return ids[Math.max(0, Math.min(ids.length - 1, index + delta))];
}

// --- периоды очереди ---

export type QueuePeriod = "all" | "day" | "week" | "month";

export const QUEUE_PERIODS: [QueuePeriod, string][] = [
  ["all", "Всё время"],
  ["day", "Сегодня"],
  ["week", "Неделя"],
  ["month", "Месяц"],
];

export function queuePeriodRange(kind: QueuePeriod, now: Date = new Date()): Partial<PeriodRange> {
  return kind === "all" ? {} : periodRange(kind, now);
}

// --- сравнение пары ---

export interface DuplicateTarget {
  id: string;
  username: string;
  receivedAt: string;
  outcome: Outcome | null;
  verdict: Verdict | null;
  thumbUrl: string | null;
  kinds: Partial<Record<MatchKind, number>>;
}

/** Совпавшие скрины, по одному на сабмишен, от самого раннего: первый — тот, у кого «взяли». */
export function duplicateTargets(item: Pick<AdminSubmission, "matches">): DuplicateTarget[] {
  const byId = new Map<string, DuplicateTarget>();
  for (const match of item.matches) {
    const target = byId.get(match.other_id) ?? {
      id: match.other_id,
      username: match.other_user.username || "удалённый сотрудник",
      receivedAt: match.other_received_at,
      outcome: match.other_outcome,
      verdict: match.other_verdict,
      thumbUrl: match.thumb_url,
      kinds: {},
    };
    target.kinds[match.kind] = Math.max(target.kinds[match.kind] ?? 0, match.score);
    target.thumbUrl = target.thumbUrl ?? match.thumb_url;
    byId.set(match.other_id, target);
  }
  return [...byId.values()].sort((a, b) => a.receivedAt.localeCompare(b.receivedAt));
}

/** С кем сравнивать: с тем, на кого уже указывает duplicate_of, иначе с самым ранним. */
export function pairTarget(item: Pick<AdminSubmission, "matches" | "duplicate_of">): DuplicateTarget | null {
  const targets = duplicateTargets(item);
  return targets.find((target) => target.id === item.duplicate_of) ?? targets[0] ?? null;
}

export function formatScore(score: number): string {
  return score.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export interface ComparisonRow {
  label: string;
  badge: Badge | null;
  note: string;
}

/**
 * Строки таблицы признаков. Совпадение по ключу кейса значит, что сошлись и чат, и время отзыва;
 * текст — по самой высокой оценке среди текстовых совпадений.
 */
export function comparePair(
  item: Pick<AdminSubmission, "user" | "outcome">,
  target: DuplicateTarget,
): ComparisonRow[] {
  const has = (...kinds: MatchKind[]) => kinds.some((kind) => target.kinds[kind] !== undefined);
  const textScore = Math.max(-1, ...(["chat_text", "text_time", "messages"] as MatchKind[]).map((kind) => target.kinds[kind] ?? -1));
  const none: Badge = { label: "не совпал", tone: "neutral" };

  const chat = has("case_key", "chat_text") ? { label: "совпал", tone: "positive" as Tone } : none;
  const time = has("case_key", "text_time") ? { label: "совпало", tone: "positive" as Tone } : { ...none, label: "не совпало" };
  const text: Badge =
    textScore >= 0
      ? { label: formatScore(textScore), tone: textScore >= 0.9 ? "positive" : "warning" }
      : { ...none, label: "не совпал" };
  let image: Badge = { ...none, label: "не похожа" };
  if (has("exact_file")) image = { label: "тот же файл", tone: "positive" };
  else if (has("near_image")) image = { label: `похожа, ${formatScore(target.kinds.near_image ?? 0)}`, tone: "warning" };

  const who = item.user.username || "удалённый сотрудник";
  return [
    { label: "Чат", badge: chat, note: "" },
    { label: "Время отзыва", badge: time, note: "" },
    { label: "Текст", badge: text, note: "" },
    { label: "Картинка (хеш)", badge: image, note: "" },
    {
      label: "Подписи",
      badge: null,
      note: `${target.username}: ${outcomeText(target.outcome)} · ${who}: ${outcomeText(item.outcome)}`,
    },
  ];
}

// --- распознавание ---

/** Уверенность модели по полю: зелёная от 0,9, жёлтая от 0,7. */
export function confidenceBadge(value: number | null | undefined): Badge | null {
  if (value === null || value === undefined) return null;
  const tone: Tone = value >= 0.9 ? "positive" : value >= 0.7 ? "warning" : "negative";
  return { label: formatScore(value), tone };
}

/** Согласие VLM и OCR по полю; порог 0,8 — как в правиле extraction_disagreement. */
export function agreementBadge(value: number | null | undefined): Badge | null {
  if (value === null || value === undefined) return null;
  return value >= 0.8
    ? { label: "VLM = OCR", tone: "positive" }
    : { label: `VLM ≠ OCR, ${formatScore(value)}`, tone: "warning" };
}

/** Хвост chatId для показа: последние 12 символов. */
export const chatTail = (chatId: string | null): string => (chatId ? `…${chatId.slice(-12)}` : "не прочитан");

const reviewFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Moscow",
});
const reviewDayFormatter = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", timeZone: "Europe/Moscow" });

export function formatReviewAt(iso: string | null, precision: "minute" | "day" | null = "minute"): string {
  if (!iso) return "не прочитано";
  return (precision === "day" ? reviewDayFormatter : reviewFormatter).format(new Date(iso)).replace(".", "");
}

// --- менеджеры ---

export const formatShare = (rate: number): string => `${Math.round(rate * 100)}%`;

/** Сигналы для разговора, а не санкции: каждый флаг — бейдж с долей, без флагов — «Норма». */
export function managerSignals(stats: ManagerStats): Badge[] {
  const badges: Badge[] = [];
  for (const flag of stats.flags) {
    if (flag === "high_duplicate_rate") badges.push({ label: `Дублей ${formatShare(stats.duplicate_rate)}`, tone: "negative" });
    if (flag === "high_review_rate") badges.push({ label: `Ревью ${formatShare(stats.review_rate)}`, tone: "warning" });
    if (flag === "high_reject_rate") badges.push({ label: `Отклонено ${formatShare(stats.reject_rate)}`, tone: "warning" });
    if (flag === "upload_burst") badges.push({ label: "Всплеск загрузок", tone: "warning" });
  }
  return badges.length ? badges : [{ label: "Норма", tone: "positive" }];
}

function csvCell(value: string | number): string {
  const text = String(value);
  return /[";\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** CSV для Excel: «;» и BOM, доли — целые проценты. */
export function managersCsv(items: ManagerStats[], period: PeriodRange): string {
  const head = [
    "Менеджер",
    "Всего",
    "Засчитано",
    "Полож",
    "Доп",
    "Удален",
    "Дубли",
    "На ревью",
    "Отклонено",
    "Доля дублей",
    "Доля ревью",
    "Сигналы",
    "Период",
  ];
  const rows = items.map((stats) => [
    stats.user.username || "удалённый сотрудник",
    stats.total,
    stats.accepted,
    stats.positive,
    stats.amended,
    stats.deleted,
    stats.duplicate,
    stats.needs_review,
    stats.rejected,
    formatShare(stats.duplicate_rate),
    formatShare(stats.review_rate),
    managerSignals(stats)
      .map((badge) => badge.label)
      .join(", "),
    `${period.from} — ${period.to}`,
  ]);
  return `﻿${[head, ...rows].map((row) => row.map(csvCell).join(";")).join("\r\n")}\r\n`;
}

export const managersCsvName = (period: PeriodRange = { from: moscowToday(), to: moscowToday() }) =>
  `screenshots-managers-${period.from}_${period.to}.csv`;

// --- конвейер ---

/** Объём хранилища: до гигабайта — как у менеджера, дальше «1,6 ГБ». */
export function formatStorage(bytes: number): string {
  if (bytes <= 0) return "0 МБ";
  const gb = bytes / 1024 ** 3;
  if (gb < 1) return formatBytes(bytes);
  return `${gb.toLocaleString("ru-RU", { maximumFractionDigits: gb < 10 ? 1 : 0 })} ГБ`;
}

/** «8 с», «4 мин», «1 ч 5 мин». */
export function formatAge(seconds: number | null): string {
  if (seconds === null) return "—";
  const total = Math.round(seconds);
  if (total < 60) return `${total} с`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} ч ${rest} мин` : `${hours} ч`;
}

export const STAGING_WARN_PERCENT = 85;
export const VLM_ERROR_WARN = 0.2;
export const OLDEST_PROCESSING_WARN_S = 600;
export const OLDEST_STAGED_WARN_S = 900;

export interface PipelineAlert {
  key: string;
  title: string;
  text: string;
}

export function pipelineAlerts(pipeline: Pipeline): PipelineAlert[] {
  const alerts: PipelineAlert[] = [];
  const staging = pipeline.storage.staging_used_percent;
  if (staging !== null && staging >= STAGING_WARN_PERCENT) {
    alerts.push({
      key: "staging",
      title: `Временное хранилище заполнено на ${Math.round(staging)}%`,
      text: "Файлы не успевают уходить в S3. Когда место кончится, приём скриншотов остановится.",
    });
  }
  const staged = pipeline.storage.oldest_staged_age_s;
  if (staged !== null && staged > OLDEST_STAGED_WARN_S) {
    alerts.push({
      key: "staged-age",
      title: `Файл ждёт выгрузки в S3 ${formatAge(staged)}`,
      text: "Похоже, S3 недоступен или событие выгрузки потерялось.",
    });
  }
  const errors = pipeline.vlm_error_rate_1h;
  if (errors !== null && errors > VLM_ERROR_WARN) {
    alerts.push({
      key: "vlm",
      title: `Ошибки распознавания за час: ${formatShare(errors)}`,
      text: "Скриншоты с ошибкой после всех попыток уходят на ручную проверку. Проверьте доступ к модели.",
    });
  }
  const oldest = pipeline.oldest_processing_age_s;
  if (oldest !== null && oldest > OLDEST_PROCESSING_WARN_S) {
    alerts.push({
      key: "queue",
      title: `Скриншот ждёт вердикта ${formatAge(oldest)}`,
      text: "Очередь стоит дольше 10 минут: проверьте воркер и Kafka.",
    });
  }
  return alerts;
}
