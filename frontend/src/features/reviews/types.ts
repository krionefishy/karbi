import type { ArticleState } from "../sellers/types";

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
  vendor_code: string;
  name: string;
  imt_id: number | null;
  brand: string;
  subject_id: number | null;
  subject_name: string;
  photo_url: string;
  state: ArticleState;
  snapshots: DailyReviewSnapshot[];
  card_snapshots: DailyReviewSnapshot[];
  photo_count: number | null;
  rating: number | null;
  rating_source: RatingSource | null;
  group_id: string | null;
  group_role: GroupRole | null;
  group_manual: boolean;
  group_snapshots: DailyReviewSnapshot[];
}

/** wb — рейтинг самой карточки из аналитики WB; group — рейтинг основного товара склейки;
 *  reviews — наш расчёт по счётчикам отзывов, когда WB рейтинг не отдал. */
export type RatingSource = "wb" | "group" | "reviews";

export type GroupRole = "main" | "member";

export type GroupAction = "pin_main" | "attach" | "detach" | "reset";

export interface SellerReviewHistory {
  seller_id: string;
  products: ProductReviewHistory[];
}

export interface ReviewSyncRun {
  id: string;
  trigger: "scheduled" | "manual";
  snapshot_date: string;
  status: "queued" | "running" | "success" | "partial_success" | "error";
  total_sellers: number;
  completed_sellers: number;
  failed_sellers: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  jobs: ReviewSyncJob[];
}

export interface ReviewSyncJob {
  id: string;
  seller_id: string;
  seller_name: string;
  status: "queued" | "running" | "success" | "error";
  product_count: number;
  feedback_count: number;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  attempts: number;
}
