export interface Seller {
  id: string;
  name: string;
  product_count: number;
  catalog_sync_status: "queued" | "success" | "syncing" | "error";
  last_catalog_sync_at: string | null;
  catalog_sync_error: string | null;
  archived_at: string | null;
  automations: string[];
  egress_status: EgressStatus;
  egress_error: string | null;
  ozon_egress_status: EgressStatus;
  ozon_egress_error: string | null;
  mpstats_egress_status: EgressStatus;
  mpstats_egress_error: string | null;
  egress_ip: string | null;
  tax_rate: number | null;
  tax_rate_from: string | null;
}

export type EgressStatus =
  | "verified"
  | "delivered"
  | "key_invalid"
  | "no_free_ip"
  | "disabled"
  | "undelivered"
  | "unsynced";

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

export interface OzonCredentialsInput {
  client_id: string;
  api_key: string;
  performance_client_id: string;
  performance_client_secret: string;
}

export const isOzonMissing = (seller: Seller) =>
  seller.ozon_egress_status === "undelivered" && seller.ozon_egress_error === null;

export interface TaxRateInput {
  rate: string;
  effective_from: string | null;
}

export interface MPStatsCredentialsInput {
  token: string;
}

export const isMPStatsMissing = (seller: Seller) =>
  seller.mpstats_egress_status === "undelivered" && seller.mpstats_egress_error === null;
