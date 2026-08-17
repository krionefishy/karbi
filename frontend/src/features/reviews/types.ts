export interface Seller {
  id: string;
  name: string;
  product_count: number;
  catalog_sync_status: "queued" | "success" | "syncing" | "error";
  last_catalog_sync_at: string | null;
  catalog_sync_error: string | null;
}

export interface SellerArticle {
  id: string;
  seller_id: string;
  article: string;
  vendor_code: string;
  name: string;
}

export interface SellerInput {
  name: string;
  api_key: string;
}

export interface RatingCounts {
  1: number;
  2: number;
  3: number;
  4: number;
  5: number;
}

export interface DailyReviewSnapshot {
  date: string;
  ratings: RatingCounts;
}

export interface ProductReviewHistory {
  id: string;
  article: string;
  name: string;
  snapshots: DailyReviewSnapshot[];
}

export interface SellerReviewHistory {
  seller_id: string;
  products: ProductReviewHistory[];
}
