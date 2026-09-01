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
  /** Сага доставки учётки на шлюз wb-egress. Пара без префикса — про WB. */
  egress_status: EgressStatus;
  egress_error: string | null;
  ozon_egress_status: EgressStatus;
  ozon_egress_error: string | null;
  /** Адрес общий: у селлера он один на оба маркетплейса. */
  egress_ip: string | null;
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

/**
 * Учётка Ozon целиком. Частичного обновления нет: шлюз заменяет учётку
 * полностью, поэтому при ротации вводятся все значения заново.
 */
export interface OzonCredentialsInput {
  client_id: string;
  api_key: string;
  performance_client_id: string;
  performance_client_secret: string;
}

/** Учётка Ozon ни разу не доезжала до шлюза — это не ошибка, а «ещё не заводили». */
export const isOzonMissing = (seller: Seller) =>
  seller.ozon_egress_status === "undelivered" && seller.ozon_egress_error === null;
