import type { BotMode, DialogState, SendStatus, SupportBot } from "./types";

export const STATE_LABEL: Record<DialogState, string> = {
  no_reply: "Не отвечал",
  awaiting_us: "Ждёт ответа",
  awaiting_buyer: "Ждём покупателя",
  in_campaign: "Идёт рассылка",
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
  live: "Отправка включена",
};

/** Порядок карточек сводки: сначала то, что требует человека. */
export const STATE_ORDER: DialogState[] = [
  "awaiting_us",
  "in_campaign",
  "awaiting_buyer",
  "exhausted",
  "no_reply",
  "closed",
];

export function totalDialogs(bot: Pick<SupportBot, "counts">): number {
  return Object.values(bot.counts).reduce((sum, value) => sum + (value ?? 0), 0);
}

/**
 * Что сейчас делает бот по кабинету, одной фразой: от самого важного к обычному.
 * Остановка по причине или общим выключателем перекрывает режим.
 */
export function statusLine(bot: Pick<SupportBot, "mode" | "paused_reason" | "globally_paused">): string {
  if (bot.globally_paused) return "Рассылка остановлена общим выключателем";
  if (bot.paused_reason) return `Отправка остановлена: ${bot.paused_reason}`;
  return bot.mode === "live" ? "Бот отправляет сообщения" : "Сухой прогон: бот считает, но ничего не отправляет";
}
