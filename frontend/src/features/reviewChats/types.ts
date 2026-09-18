/** С нашим сообщением следом за автосообщением WB и без него. */
export type DialogGroup = "followed" | "bare";
/** `pending` — окно ответа ещё не истекло, молчание пока ничего не значит. */
export type DialogOutcome = "replied" | "silent" | "pending";

export interface GroupSummary {
  total: number;
  replied: number;
  silent: number;
  pending: number;
  /** Доли от диалогов с известным исходом; null — таких ещё нет. */
  reply_rate: number | null;
  silent_rate: number | null;
}

export interface DaySummary {
  day: string;
  followed: GroupSummary;
  bare: GroupSummary;
}

export interface Dialog {
  chat_id: string;
  prompt_at: string;
  nm_id: number | null;
  product_name: string;
  group: DialogGroup;
  outcome: DialogOutcome;
  follow_up_at: string | null;
  follow_up_text: string | null;
  /** Наше сообщение ушло уже после ответа покупателя. */
  follow_up_late: boolean;
  reply_at: string | null;
  reply_text: string | null;
  reply_has_attachments: boolean;
}

export interface ReviewChats {
  seller_id: string;
  seller_name: string;
  date_from: string;
  date_to: string;
  reply_window_hours: number;
  followed: GroupSummary;
  bare: GroupSummary;
  follow_up_late: number;
  first_follow_up_at: string | null;
  days: DaySummary[];
  dialogs: Dialog[];
  page: number;
  page_size: number;
  total_dialogs: number;
  history_from: string | null;
  synced_through: string | null;
  collection_error: string | null;
}

export interface ReviewChatsFilter {
  dateFrom: string;
  dateTo: string;
  group: DialogGroup | "";
  outcome: DialogOutcome | "";
  page: number;
}
