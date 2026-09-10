/** auto — решает WB, confirm — менеджер, когда WB видит факт, manual — только менеджер. */
export type ItemKind = "auto" | "confirm" | "manual";

export interface ChecklistItem {
  key: string;
  title: string;
  kind: ItemKind;
  meaning: string;
}

export interface ItemState {
  key: string;
  kind: ItemKind;
  checked: boolean;
  /** Можно ли поставить отметку. Снять свою можно всегда. */
  can_check: boolean;
  /** Что видит WB: «25/38», «8 с фото». */
  detail: string | null;
  note: string | null;
  /** Отметка стоит, а факт, на котором она держалась, пропал. */
  warning: string | null;
  /** Данных нет — «не знаем», а не «не выполнено». */
  unknown: boolean;
}

export interface ChecklistRow {
  article: string;
  vendor_code: string;
  barcode: string;
  title: string;
  photo_url: string;
  subject_name: string;
  card_created_at: string | null;
  stock: number;
  items: ItemState[];
  done: number;
  ready: boolean;
  comment: string;
}

export type StockState = "ok" | "not_connected" | "stale";
export type ReviewsState = "ok" | "not_connected" | "no_snapshot";

export interface Checklist {
  seller_id: string;
  seller_name: string;
  collected_at: string | null;
  collection_error: string | null;
  stock_state: StockState;
  reviews_state: ReviewsState;
  min_stock: number;
  items: ChecklistItem[];
  rows: ChecklistRow[];
}

export interface RefreshState {
  status: "queued" | "running" | "success" | "error";
  in_progress: boolean;
  requested_at: string;
  finished_at: string | null;
  error: string | null;
}
