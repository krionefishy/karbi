import type {
  ArmStats,
  BotDialog,
  BotMode,
  DailyPoint,
  DialogState,
  ManualVerdict,
  ReplyBucket,
  ReplyCategoryCount,
  ReplyGateMode,
  SendStatus,
  SupportBot,
  Verdict,
} from "./types";

export const STATE_LABEL: Record<DialogState, string> = {
  no_reply: "Не отвечал",
  awaiting_us: "Ждёт ответа",
  awaiting_buyer: "Ждём покупателя",
  in_campaign: "Идёт рассылка",
  product_removed: "Товар снят",
  exhausted: "Рассылка завершена",
  closed: "Закрыт",
};

export const SEND_LABEL: Record<SendStatus, string> = {
  planned: "Запланировано",
  sending: "Отправляется",
  sent: "Отправлено",
  unknown: "Неизвестно",
  failed: "Не ушло",
  dry_run: "Сухой прогон",
};

export const MODE_LABEL: Record<BotMode, string> = {
  dry_run: "Сухой прогон",
  canary: "Канарейка",
  live: "Отправка включена",
};

export const STATE_ORDER: DialogState[] = [
  "awaiting_us",
  "in_campaign",
  "awaiting_buyer",
  "product_removed",
  "exhausted",
  "no_reply",
  "closed",
];

export function totalDialogs(bot: Pick<SupportBot, "counts">): number {
  return Object.values(bot.counts).reduce((sum, value) => sum + (value ?? 0), 0);
}

export function statusLine(
  bot: Pick<SupportBot, "mode" | "paused_reason" | "globally_paused"> & { canary_percent?: number },
): string {
  if (bot.globally_paused) return "Рассылка остановлена общим выключателем";
  if (bot.paused_reason) return `Отправка остановлена: ${bot.paused_reason}`;
  if (bot.mode === "live") return "Бот отправляет сообщения";
  if (bot.mode === "canary") {
    return `Канарейка: сообщения уходят только по ${bot.canary_percent ?? 10}% диалогов, по остальным сухой прогон`;
  }
  return "Сухой прогон: бот считает, но ничего не отправляет";
}

export const VERDICT_LABEL: Record<Verdict, string> = {
  open: "ИИ: ждёт ответа",
  closing: "ИИ: диалог завершён",
  unclear: "ИИ: неясно",
};

export const MANUAL_LABEL: Record<ManualVerdict, string> = {
  allow: "вручную: писать",
  deny: "вручную: не писать",
};

export function verdictNote(dialog: Pick<BotDialog, "verdict" | "verdict_reason" | "manual_verdict">): string | null {
  if (dialog.manual_verdict) return MANUAL_LABEL[dialog.manual_verdict];
  if (!dialog.verdict) return null;
  const label = VERDICT_LABEL[dialog.verdict];
  return dialog.verdict_reason ? `${label} (${dialog.verdict_reason})` : label;
}

export function needsDecision(
  dialog: Pick<BotDialog, "state" | "verdict" | "manual_verdict">,
  mode: SupportBot["classifier_mode"],
): boolean {
  if (mode === "off" || dialog.state !== "in_campaign") return false;
  return dialog.manual_verdict !== null || dialog.verdict !== "open";
}

export const REPLY_LABEL: Record<ReplyBucket, string> = {
  threat: "Угрозы, жалобы, агрессия",
  stop_request: "Просьба прекратить",
  thanks: "Благодарность",
  cant_delete: "Не получается удалить отзыв",
  refuses: "Отказ",
  not_helped: "Мне не помогли",
  noise: "Пустой ответ",
  waiting_seller: "Ждёт решения продавца",
  sent_details: "Прислал данные",
  question: "Вопрос",
  says_deleted: "Я удалил отзыв",
  unclear: "Не понятно",
  stopword: "Стоп-слово",
  attachment: "Вложение",
  undetermined: "Не определено",
};

export const REPLY_ORDER: ReplyBucket[] = [
  "threat",
  "stop_request",
  "thanks",
  "cant_delete",
  "refuses",
  "not_helped",
  "noise",
  "waiting_seller",
  "sent_details",
  "question",
  "says_deleted",
  "unclear",
  "stopword",
  "attachment",
  "undetermined",
];

export type ReplyTone = "stop" | "warn" | "go" | "neutral";

export function replyTone(bucket: ReplyBucket): ReplyTone {
  if (bucket === "threat") return "stop";
  if (bucket === "stop_request") return "warn";
  if (bucket === "refuses" || bucket === "not_helped" || bucket === "noise") return "go";
  return "neutral";
}

export function replyLabel(bucket: string): string {
  return REPLY_LABEL[bucket as ReplyBucket] ?? bucket;
}

export const GATE_NOTE: Record<ReplyGateMode, string> = {
  off: "Слой ответов выключен",
  shadow: "Только запись классов, рассылка не меняется",
  enforce: "Слой ответов управляет рассылкой",
};

/** Доля в процентах с одним знаком; «—» при нулевом знаменателе. */
export function percent(part: number, total: number): string {
  if (total <= 0) return "—";
  return `${((part / total) * 100).toFixed(1)}%`;
}

/** Интервал Уилсона 95% для доли, значения в долях [0, 1]. */
export function wilson(successes: number, total: number): [number, number] {
  if (total <= 0) return [0, 0];
  const z = 1.96;
  const p = successes / total;
  const z2 = z * z;
  const denom = 1 + z2 / total;
  const centre = (p + z2 / (2 * total)) / denom;
  const margin = (z * Math.sqrt((p * (1 - p)) / total + z2 / (4 * total * total))) / denom;
  return [Math.max(0, centre - margin), Math.min(1, centre + margin)];
}

export function wilsonText(successes: number, total: number): string {
  if (total <= 0) return "—";
  const [low, high] = wilson(successes, total);
  return `${(low * 100).toFixed(1)}–${(high * 100).toFixed(1)}%`;
}

export function cumulative(values: number[]): number[] {
  let sum = 0;
  return values.map((value) => (sum += value));
}

export interface ArmRates {
  rate24: number | null;
  rate48: number | null;
}

/** Перевод ArmStats в доли ответивших; null, если некого считать. */
export function armRates(arm: ArmStats): ArmRates {
  return {
    rate24: arm.eligible24 > 0 ? arm.replied24 / arm.eligible24 : null,
    rate48: arm.eligible48 > 0 ? arm.replied48 / arm.eligible48 : null,
  };
}

export interface DailySeries {
  dates: string[];
  live: number[];
  control: number[];
  cumulative: number[];
  rate48Live: (number | null)[];
  rate48Control: (number | null)[];
}

/** Ряды для графика «Ответы по дням»; доля 48 ч только в днях, где были старты. */
export function dailySeries(daily: DailyPoint[]): DailySeries {
  const live = daily.map((day) => day.replies_live);
  const control = daily.map((day) => day.replies_control);
  return {
    dates: daily.map((day) => day.date),
    live,
    control,
    cumulative: cumulative(daily.map((day) => day.replies_live + day.replies_control)),
    rate48Live: daily.map((day) => (day.started_live > 0 ? day.replied48_live / day.started_live : null)),
    rate48Control: daily.map((day) => (day.started_control > 0 ? day.replied48_control / day.started_control : null)),
  };
}

/** Классы по порядку REPLY_ORDER, только с ненулевым счётом. */
export function sortedCategories(items: ReplyCategoryCount[]): ReplyCategoryCount[] {
  const rank = (bucket: ReplyBucket) => {
    const at = REPLY_ORDER.indexOf(bucket);
    return at === -1 ? REPLY_ORDER.length : at;
  };
  return items.filter((item) => item.count > 0).sort((a, b) => rank(a.category) - rank(b.category));
}

/** Задержка в минутах: «—», «5 мин», «2 ч 05 мин», «1 д 3 ч». */
export function formatMinutes(minutes: number | null): string {
  if (minutes === null || !Number.isFinite(minutes)) return "—";
  const total = Math.round(minutes);
  if (total < 60) return `${total} мин`;
  if (total < 1440) {
    const hours = Math.floor(total / 60);
    const rest = total % 60;
    return rest ? `${hours} ч ${String(rest).padStart(2, "0")} мин` : `${hours} ч`;
  }
  const days = Math.floor(total / 1440);
  const hours = Math.floor((total % 1440) / 60);
  return hours ? `${days} д ${hours} ч` : `${days} д`;
}
