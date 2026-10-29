export type DialogState =
  | "no_reply"
  | "awaiting_us"
  | "awaiting_buyer"
  | "in_campaign"
  | "product_removed"
  | "exhausted"
  | "closed";
export type BotMode = "dry_run" | "live";
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
  classifier_mode: ClassifierMode;
  reply_gate_mode: ReplyGateMode;
  monitor_overall: Severity;
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
  reply_hold: boolean;
  reply_category: ReplyCategory | null;
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

export type ReplyGateMode = "off" | "shadow" | "enforce";
export type ReplyCategory =
  | "threat"
  | "stop_request"
  | "thanks"
  | "cant_delete"
  | "refuses"
  | "not_helped"
  | "noise"
  | "waiting_seller"
  | "sent_details"
  | "question"
  | "says_deleted"
  | "unclear";
export type ReplyBucket = ReplyCategory | "stopword" | "attachment" | "undetermined";
export type ReplyAction = "continue" | "stop" | "wait";

export interface ArmStats {
  dialogs: number;
  eligible24: number;
  replied24: number;
  eligible48: number;
  replied48: number;
}

export interface ReplyCategoryCount {
  category: ReplyBucket;
  count: number;
}

export interface DailyPoint {
  date: string;
  started_live: number;
  started_control: number;
  replied48_live: number;
  replied48_control: number;
  replies_live: number;
  replies_control: number;
}

export interface ReplySummary {
  generated_at: string;
  days: number;
  gate_mode: ReplyGateMode;
  live_since: string | null;
  effect: { live: ArmStats; control: ArmStats };
  categories: ReplyCategoryCount[];
  categories_slices: ReplyCategoryCount[];
  categories_before: ReplyCategoryCount[];
  categories_after: ReplyCategoryCount[];
  actions: { continue: number; stop: number; wait: number };
  daily: DailyPoint[];
  escalations_open: number;
  late_sends: {
    count: number;
    total_sends: number;
    median_lag_minutes: number | null;
    max_lag_minutes: number | null;
  };
  continues: {
    dialogs: number;
    escalated_after: number;
    resolved_after: number;
    max_per_dialog: number;
    distribution: { count: number; dialogs: number }[];
  };
  health: {
    total: number;
    pending: number;
    stuck: number;
    failed: number;
    unclear: number;
    median_decision_minutes: number | null;
    tokens_in: number;
    tokens_out: number;
    calls: number;
  };
}

export interface EscalationItem {
  chat_id: string;
  prompt_at: string;
  nm_id: number | null;
  category: ReplyBucket;
  reason: string | null;
  event_at: string;
  state: string;
  closed: boolean;
  excluded: boolean;
  chat_url: string | null;
}

export interface ReplyDialogMessage {
  at: string;
  role: "buyer" | "seller" | "bot" | "wb";
  text: string;
}

export interface ReplyVerdict {
  first_event_at: string;
  last_event_at: string;
  messages: number;
  status: "pending" | "done" | "failed";
  category: ReplyBucket | null;
  reason: string | null;
  action: ReplyAction;
}

export interface ReplyDialog {
  chat_id: string;
  prompt_at: string;
  chat_url: string | null;
  messages: ReplyDialogMessage[];
  verdicts: ReplyVerdict[];
}

export type Severity = "ok" | "warn" | "crit" | "nodata";
export type MonitorGroup = "delivery" | "model" | "safety";

export interface MonitorCheck {
  id: string;
  group: MonitorGroup;
  title: string;
  severity: Severity;
  value: string | null;
  threshold: string;
  hint: string;
}

export interface MonitorIncident {
  check_id: string;
  title: string;
  severity: "warn" | "crit";
  started_at: string;
  ended_at: string | null;
  value: string | null;
}

export interface MonitorDay {
  date: string;
  warn_minutes: number;
  crit_minutes: number;
}

export interface MonitorResponse {
  checked_at: string;
  worker_checked_at: string | null;
  overall: Severity;
  checks: MonitorCheck[];
  incidents: MonitorIncident[];
  days: MonitorDay[];
}
