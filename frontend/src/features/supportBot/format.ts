import type { BotDialog, BotMode, DialogState, ManualVerdict, SendStatus, SupportBot, Verdict } from "./types";

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
