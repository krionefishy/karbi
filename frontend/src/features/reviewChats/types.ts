/**
 * Куда попал диалог относительно нашей рассылки: наше сообщение ушло следом; покупатель
 * написал раньше нашего сообщения (после запуска); пропуск рассылки; до запуска.
 */
export type DialogGroup = "followed" | "early" | "missed" | "before";
/** `pending` — окно ответа ещё не истекло, молчание пока ничего не значит. */
export type DialogOutcome = "replied" | "silent" | "pending";

export interface GroupSummary {
  total: number;
  replied: number;
  silent: number;
  pending: number;
  /** Доли от всех диалогов; пока есть «ждём», доля ответивших может только вырасти. null — диалогов нет. */
  reply_rate: number | null;
  silent_rate: number | null;
  pending_rate: number | null;
}

export interface DaySummary {
  day: string;
  total: GroupSummary;
  followed: GroupSummary;
  early: GroupSummary;
  missed: GroupSummary;
}

export interface Baseline {
  date_from: string;
  date_to: string;
  summary: GroupSummary;
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
  /** Момент запуска рассылки по кабинету; null — наше сообщение ещё не уходило. */
  launch_at: string | null;
  followed: GroupSummary;
  early: GroupSummary;
  missed: GroupSummary;
  before: GroupSummary;
  /** Все диалоги периода после запуска, в какую бы группу они ни попали. */
  after_launch: GroupSummary;
  /** Две недели до дня запуска — не зависит от выбранного периода. */
  baseline: Baseline | null;
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
