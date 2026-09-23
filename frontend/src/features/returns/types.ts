export type ReturnStatusKey =
  | "ready"
  | "transit"
  | "issued"
  | "not_picked"
  | "expired"
  | "cancelled"
  | "created"
  | "unknown";

export interface ReturnItem {
  shk_id: number;
  sticker_id: string;
  srid: string;
  order_id: number;
  nm_id: number;
  barcode: string;
  /** Предмет · бренд · размер — как в сообщении бота. */
  title: string;
  brand: string;
  subject_name: string;
  tech_size: string;
  return_type: string;
  reason: string;
  status: string;
  status_key: ReturnStatusKey;
  status_title: string;
  dst_office_id: number | null;
  dst_office_address: string;
  order_dt: string | null;
  status_changed_at: string;
  /** От какого момента считается хранение в ПВЗ; только у готовых к выдаче. */
  ready_at: string | null;
  free_until: string | null;
  pickup_deadline: string | null;
}

export interface Claim {
  id: string;
  nm_id: number;
  name: string;
  user_comment: string;
  wb_comment: string;
  status: number;
  status_ex: number;
  is_archive: boolean;
  price: number;
  currency_code: string;
  srid: string;
  photos: string[];
  videos: string[];
  actions: string[];
  created_at: string;
  order_dt: string | null;
  delivery_dt: string | null;
  review_deadline: string;
}

export interface Returns {
  seller_id: string;
  seller_name: string;
  collected_at: string | null;
  collection_error: string | null;
  ready: ReturnItem[];
  transit: ReturnItem[];
  other_active: ReturnItem[];
  history: ReturnItem[];
  claims: Claim[];
  claims_history: Claim[];
}

export interface InviteLink {
  url: string;
  expires_at: string;
}

export interface RefreshState {
  status: "queued" | "running" | "success" | "error";
  in_progress: boolean;
  requested_at: string;
  finished_at: string | null;
  error: string | null;
}
