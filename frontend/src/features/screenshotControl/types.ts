// Контракт «Контроля скриншотов ДБС». Источник — backend/modules/wb_screenshot_control/presentation/http/schemas.py;
// типы повторяют его вручную, при правке схем правится и этот файл. Полная схема:
// `just openapi --tag wb-screenshot-control`. Время — ISO-8601 UTC, периоды — даты по Москве (YYYY-MM-DD).
// Машинный код ошибки приходит заголовком X-Error-Code (ErrorCode), текст — в detail.

export type Outcome = "positive" | "amended" | "deleted";
export type OutcomeSource = "button" | "caption" | "form" | "admin";
export type Verdict = "accepted" | "duplicate" | "needs_review" | "rejected";
export type Status = "awaiting_outcome" | "processing" | Verdict;
export type Channel = "extension" | "telegram" | "web";
export type Stage = "processing" | "extracted" | "decided";
export type Mode = "off" | "shadow" | "enforce";
export type Severity = "info" | "warn" | "high";
export type Suspicion = "high" | "medium" | "low";
export type ImageKind = "thumb" | "original";
export type Layout = "desktop_window" | "desktop_viewport" | "mobile_app" | "other" | "unreadable";
export type CaseKind = "desktop" | "mobile";
export type ChatIdSource = "url" | "image";
export type TimePrecision = "minute" | "day";
export type ExtractionStatus = "ok" | "failed";
export type StorageState = "staged" | "uploading" | "stored" | "delete_requested" | "deleted" | "failed";

export type ReasonCode =
  | "exact_file"
  | "case_key"
  | "near_image"
  | "near_image_conflict"
  | "cross_user_duplicate"
  | "caption_conflict"
  | "same_chat_same_text_other_time"
  | "same_chat_similar_text"
  | "same_text_time_other_chat"
  | "same_text_other_time"
  | "messages_overlap"
  | "possible_duplicate"
  | "order_conflict"
  | "weak_key"
  | "extraction_disagreement_chat_id"
  | "extraction_disagreement_time"
  | "extraction_disagreement_text"
  | "extraction_failed"
  | "chat_id_unreadable"
  | "review_time_unreadable"
  | "review_date_implausible"
  | "url_not_chat"
  | "not_wb_screenshot"
  | "unreadable"
  | "cropped_missing_panel"
  | "photo_of_screen"
  | "possible_tampering"
  | "late_upload"
  | "clock_skew"
  | "upload_burst"
  | "no_outcome"
  | "audit_sample";

export type ManagerReasonCode =
  | "already_uploaded"
  | "under_review"
  | "unreadable"
  | "cropped_missing_panel"
  | "not_wb_screenshot"
  | "no_outcome";

export type MatchKind = "exact_file" | "near_image" | "case_key" | "chat_text" | "text_time" | "messages";
export type ResolveDecision = "approve" | "decline" | "duplicate_of" | "not_duplicate" | "audit_ok";
export type QueueKind = "review" | "duplicates" | "audit" | "all";
export type ActorKind = "system" | "user" | "admin";
export type ManagerFlag = "high_duplicate_rate" | "high_review_rate" | "high_reject_rate" | "upload_burst";

export type ErrorCode =
  | "not_found"
  | "file_too_large"
  | "unsupported_type"
  | "too_small"
  | "bad_image"
  | "bad_outcome"
  | "outcome_required"
  | "idempotency_conflict"
  | "outcome_locked"
  | "period_too_long"
  | "storage_full"
  | "rate_limited"
  | "limiter_unavailable"
  | "module_off"
  | "code_invalid"
  | "bad_token"
  | "install_revoked"
  | "user_blocked"
  | "upgrade_required"
  | "exact_key_conflict"
  | "note_required"
  | "duplicate_target_invalid"
  | "telegram_disabled";

export interface ErrorResponse {
  detail: string;
}

// --- менеджер ---

export interface Period {
  from: string;
  to: string;
}

export interface SubmissionFields {
  review_at: string | null;
  stars: number | null;
  text_preview: string | null;
  /** Последние 6 символов chatId. */
  chat_tail: string | null;
}

export interface Submission {
  id: string;
  channel: Channel;
  received_at: string;
  outcome: Outcome | null;
  status: Status;
  reason_code: ManagerReasonCode | null;
  reason_text: string | null;
  duplicate_received_at: string | null;
  fields: SubmissionFields | null;
  /** Presigned GET миниатюры, живёт несколько минут. */
  thumb_url: string | null;
}

export interface SubmissionPage {
  items: Submission[];
  next_cursor: string | null;
}

export interface OutcomeCounts {
  positive: number;
  amended: number;
  deleted: number;
  accepted_total: number;
}

export interface Summary {
  period: Period;
  mode: Mode;
  preliminary: boolean;
  counts: OutcomeCounts;
  pending_review: number;
  awaiting_outcome: number;
  processing: number;
  duplicates: number;
  rejected: number;
}

export interface ImageUrl {
  url: string | null;
  expires_at: string | null;
  available: boolean;
}

export interface OutcomeOption {
  value: Outcome;
  caption: string;
  label: string;
}

export interface ManagerConfig {
  mode: Mode;
  telegram_enabled: boolean;
  outcomes: OutcomeOption[];
  max_upload_bytes: number;
  accepted_types: string[];
  min_side_px: number;
  extension_url: string;
  extension_min_version: string;
  bot_username: string | null;
}

export interface PairingCode {
  /** 8 символов Crockford base32. */
  code: string;
  expires_at: string;
}

export interface ExtensionInstall {
  id: string;
  browser: string;
  ext_version: string | null;
  created_at: string;
  last_seen_at: string | null;
  last_used_at: string | null;
}

export interface TelegramLink {
  url: string;
  expires_at: string;
}

export interface TelegramStatus {
  enabled: boolean;
  linked: boolean;
  username: string | null;
  linked_at: string | null;
}

export interface SubmissionFilters {
  status?: Status;
  outcome?: Outcome;
  from?: string;
  to?: string;
  cursor?: string;
  limit?: number;
}

export interface WebSubmission {
  file: File;
  clientSubmissionId: string;
  outcome?: Outcome | null;
  clientCapturedAt?: string;
}

// --- расширение (Bearer mas_…, заголовок X-Extension-Version) ---

export interface ExtensionPairRequest {
  code: string;
  install_id: string;
  browser?: string;
  ext_version: string;
}

export interface ExtensionPair {
  token: string;
  username: string;
  min_version: string;
  allowed_url_prefixes: string[];
}

export interface ExtensionMe {
  username: string;
  min_version: string;
  mode: Mode;
  allowed_url_prefixes: string[];
  summary: Summary;
  server_time: string;
}

export interface ExtensionSubmissionList {
  items: Submission[];
}

// --- админ ---

export interface UserRef {
  id: string;
  username: string;
}

export interface Reason {
  code: ReasonCode;
  severity: Severity;
  detail: string | null;
  other_submission_id: string | null;
}

export interface Match {
  other_id: string;
  other_user: UserRef;
  other_received_at: string;
  other_outcome: Outcome | null;
  other_verdict: Verdict | null;
  kind: MatchKind;
  score: number;
  thumb_url: string | null;
}

export interface FieldConfidence {
  chat_id: number | null;
  time: number | null;
  text: number | null;
}

export interface QualityFlags {
  blurred: boolean;
  cropped_missing_panel: boolean;
  photo_of_screen: boolean;
  moire_or_glare: boolean;
  looks_edited: boolean;
  dark_overlay_or_popup: boolean;
}

export interface ClientMessage {
  text: string;
  at: string | null;
  time_precision: TimePrecision | null;
}

export interface AdminFields {
  layout: Layout | null;
  case_kind: CaseKind | null;
  chat_id: string | null;
  chat_id_source: ChatIdSource | null;
  key_time: string | null;
  key_time_precision: TimePrecision | null;
  review_at: string | null;
  review_text: string | null;
  review_text_truncated: boolean;
  stars: number | null;
  article: string | null;
  client_messages: ClientMessage[];
  buyer_name_seen: boolean;
  quality: QualityFlags | null;
  confidence: FieldConfidence;
}

export interface AdminSubmission {
  id: string;
  user: UserRef;
  channel: Channel;
  received_at: string;
  outcome: Outcome | null;
  outcome_source: OutcomeSource | null;
  caption_raw: string | null;
  stage: Stage;
  verdict: Verdict | null;
  auto_verdict: Verdict | null;
  reasons: Reason[];
  suspicion: Suspicion | null;
  duplicate_of: string | null;
  audit_requested: boolean;
  matches: Match[];
  fields: AdminFields | null;
  agreement: FieldConfidence | null;
  thumb_url: string | null;
}

export interface ClientMeta {
  ext_version: string | null;
  viewport_w: number | null;
  viewport_h: number | null;
  dpr: number | null;
  user_agent: string | null;
  telegram_compressed: boolean;
}

export interface FileMeta {
  has_exif: boolean;
  exif_software: string | null;
  has_alpha: boolean;
}

export interface StorageObject {
  kind: "orig" | "thumb";
  state: StorageState;
  stored_at: string | null;
  deleted_at: string | null;
}

export interface FileInfo {
  id: string;
  sha256: string;
  mime: string;
  size_bytes: number;
  width: number;
  height: number;
  meta: FileMeta;
  objects: StorageObject[];
  submissions_count: number;
}

export interface Extraction {
  status: ExtractionStatus;
  pipeline_version: string;
  vlm_model: string | null;
  prompt_version: string | null;
  attempts: number;
  error: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  latency_ms: number | null;
  created_at: string;
}

export interface Actor {
  kind: ActorKind;
  user: UserRef | null;
}

export interface VerdictEvent {
  at: string;
  actor: Actor;
  from_verdict: Verdict | null;
  to_verdict: Verdict | null;
  reasons: Reason[];
  note: string | null;
}

export interface AdminSubmissionDetail extends AdminSubmission {
  client_url: string | null;
  client_captured_at: string | null;
  client_tz_offset_min: number | null;
  client_meta: ClientMeta;
  install_id: string | null;
  file: FileInfo;
  extraction: Extraction | null;
  events: VerdictEvent[];
  decided_at: string | null;
  reviewed_by: UserRef | null;
  reviewed_at: string | null;
  review_note: string | null;
}

export interface QueueCounts {
  review: number;
  duplicates: number;
  audit: number;
}

export interface AdminQueuePage {
  items: AdminSubmission[];
  next_cursor: string | null;
  counts: QueueCounts;
}

export interface QueueFilters {
  kind?: QueueKind;
  reason?: ReasonCode;
  user_id?: string;
  from?: string;
  to?: string;
  cursor?: string;
  limit?: number;
}

export interface ResolveRequest {
  decision: ResolveDecision;
  /** Обязателен тогда и только тогда, когда decision = "duplicate_of". */
  duplicate_of?: string | null;
  outcome?: Outcome | null;
  note?: string;
}

export interface Reprocess {
  id: string;
  stage: Stage;
}

export interface FileErase {
  id: string;
  objects: StorageObject[];
}

export interface ManagerStats {
  user: UserRef;
  total: number;
  accepted: number;
  positive: number;
  amended: number;
  deleted: number;
  duplicate: number;
  needs_review: number;
  rejected: number;
  processing: number;
  duplicate_rate: number;
  review_rate: number;
  reject_rate: number;
  last_submission_at: string | null;
  flags: ManagerFlag[];
}

export interface Managers {
  period: Period;
  items: ManagerStats[];
}

export interface Pipeline {
  mode: Mode;
  telegram_enabled: boolean;
  stages: { processing: number; extracted: number };
  oldest_processing_age_s: number | null;
  p50_s: number | null;
  p95_s: number | null;
  vlm_error_rate_1h: number | null;
  extraction_failed_today: number;
  tokens_today: number;
  storage: {
    objects: Partial<Record<StorageState, number>>;
    stored_bytes: number;
    oldest_staged_age_s: number | null;
    staging_used_percent: number | null;
  };
  channels_today: Record<Channel, number>;
}

export interface Thresholds {
  text_dup: number;
  text_maybe: number;
  min_text_len: number;
  vlm_ocr_text: number;
  near_dhash_strict: number;
  near_tile_strict: number;
  near_dhash_soft: number;
  near_tile_soft: number;
}

export interface AdminConfig {
  mode: Mode;
  kpi_from: string | null;
  telegram_enabled: boolean;
  pipeline_version: string;
  vision_model: string | null;
  s3_bucket: string;
  retention_original_days: number;
  retention_thumb_days: number;
  retention_fields_days: number;
  dedupe_window_days: number;
  outcome_ttl_hours: number;
  review_age_max_days: number;
  max_upload_bytes: number;
  min_side_px: number;
  uploads_per_minute: number;
  uploads_per_hour: number;
  burst_per_hour: number;
  audit_percent: number;
  thresholds: Thresholds;
  extension_min_version: string;
  allowed_url_prefixes: string[];
}

export interface AdminTelegramLink {
  id: string;
  user: UserRef;
  telegram_username: string;
  linked_at: string;
}
