export type DocumentState = "rejected" | "missing" | "expiring" | "mismatch" | "ok";

export interface CardDocumentItem {
  nm_id: number;
  vendor_code: string;
  title: string;
  subject_name: string;
  state: DocumentState;
  reason: string | null;
  document_number: string | null;
  document_start: string | null;
  document_end: string | null;
  days_left: number | null;
  checked_at: string | null;
  since: string | null;
}

export interface CardDocumentsSummary {
  total: number;
  ok: number;
  rejected: number;
  missing: number;
  expiring: number;
  mismatch: number;
}

export interface CardDocuments {
  collected_at: string | null;
  last_error: string | null;
  stale: boolean;
  summary: CardDocumentsSummary;
  items: CardDocumentItem[];
}

export interface RefreshState {
  status: "queued" | "running" | "success" | "error";
  in_progress: boolean;
  requested_at: string;
  finished_at: string | null;
  error: string | null;
}

export interface InviteLink {
  url: string;
  expires_at: string;
}
