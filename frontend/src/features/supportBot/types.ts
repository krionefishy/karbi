export type DialogState =
  | "no_reply"
  | "awaiting_us"
  | "awaiting_buyer"
  | "in_campaign"
  | "product_removed"
  | "exhausted"
  | "closed";
export type BotMode = "dry_run" | "canary" | "live";
export type DialogFlag = "no_contact" | "attention" | "excluded";
export type SendStatus =
  "planned" | "sending" | "sent" | "unknown" | "failed" | "dry_run";

export interface Template {
  id: number;
  text: string;
  active: boolean;
}

export interface SupportBot {
  seller_id: string;
  seller_name: string;
  mode: BotMode;
  paused_reason: string | null;
  globally_paused: boolean;
  counts: Partial<Record<DialogState, number>>;
  templates: Template[];
  mirror_error: string | null;
  mirror_read_through: string | null;
  canary_percent: number;
  classifier_mode: ClassifierMode;
}

export type ClassifierMode = "off" | "shadow" | "enforce";
export type Verdict = "open" | "closing" | "unclear";
export type ManualVerdict = "allow" | "deny";

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
  verdict: Verdict | null;
  verdict_reason: string | null;
  manual_verdict: ManualVerdict | null;
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
  nm_id: number | null;
  chat_url: string | null;
  seq: number;
  text: string;
  status: SendStatus;
  planned_at: string;
  sent_at: string | null;
  error: string | null;
}

export type JournalStatus = "sent" | "dry_run" | "failed" | "unknown";

export interface SendPage {
  sends: BotSend[];
  total: number;
  page: number;
  page_size: number;
  counts: Partial<Record<JournalStatus, number>>;
}

export interface JournalFilter {
  status: JournalStatus | "";
  page: number;
}

export interface DialogFilter {
  state: DialogState | "";
  flag: DialogFlag | "";
  page: number;
}
