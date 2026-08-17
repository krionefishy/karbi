export interface Seller {
  id: string;
  name: string;
  product_count: number;
  sync_status: "success" | "syncing" | "error";
  last_sync_at: string;
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
