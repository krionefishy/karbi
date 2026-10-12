/**
 * Состояние диалога бота: `no_reply` — покупатель не писал; `awaiting_us` — ход за нашей
 * стороной; `awaiting_buyer` — ждём покупателя; `in_campaign` — идёт рассылка;
 * `exhausted` — рассылка отработала без ответа; `closed` — закрыт вручную.
 */
export type DialogState = "no_reply" | "awaiting_us" | "awaiting_buyer" | "in_campaign" | "exhausted" | "closed";
export type BotMode = "dry_run" | "live";
export type DialogFlag = "no_contact" | "attention" | "excluded";
export type SendStatus = "planned" | "sending" | "sent" | "unknown" | "failed" | "dry_run";

export interface Template {
  id: number;
  text: string;
  active: boolean;
}

export interface SupportBot {
  seller_id: string;
  seller_name: string;
  mode: BotMode;
  /** Причина, по которой бот сам остановил отправку по кабинету (например, ключ без права записи). */
  paused_reason: string | null;
  globally_paused: boolean;
  counts: Partial<Record<DialogState, number>>;
  templates: Template[];
  mirror_error: string | null;
  mirror_read_through: string | null;
}

export interface BotDialog {
  chat_id: string;
  prompt_at: string;
  state: DialogState;
  nm_id: number | null;
  buyer_first_at: string | null;
  buyer_last_at: string | null;
  our_last_at: string | null;
  campaign_started_at: string | null;
  next_send_at: string | null;
  sent_count: number;
  last_sent_at: string | null;
  no_contact: boolean;
  attention: boolean;
  excluded: boolean;
  closed: boolean;
  chat_url: string | null;
}

export interface DialogPage {
  dialogs: BotDialog[];
  total: number;
  page: number;
  page_size: number;
}

export interface BotSend {
  id: number;
  chat_id: string;
  prompt_at: string;
  seq: number;
  text: string;
  status: SendStatus;
  planned_at: string;
  sent_at: string | null;
  error: string | null;
}

export interface DialogFilter {
  state: DialogState | "";
  flag: DialogFlag | "";
  page: number;
}
