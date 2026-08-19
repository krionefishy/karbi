export interface Seller {
  id: string;
  name: string;
  product_count: number;
  catalog_sync_status: "queued" | "success" | "syncing" | "error";
  last_catalog_sync_at: string | null;
  catalog_sync_error: string | null;
  /** Set once the seller is retired: he keeps his history and collects nothing. */
  archived_at: string | null;
  /** Automations the seller is connected to. */
  automations: string[];
}

export type ArticleState = "active" | "archived" | "feedback_only";

export interface SellerArticle {
  id: string;
  seller_id: string;
  article: string;
  vendor_code: string;
  name: string;
  imt_id: number | null;
  brand: string;
  subject_name: string;
  photo_url: string;
  state: ArticleState;
}

export interface SellerInput {
  name: string;
  api_key: string;
}
