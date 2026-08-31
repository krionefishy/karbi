export type TurnoverStatus = "ok" | "no_stock" | "no_sales" | "insufficient_data";

export interface ArticleTurnover {
  article: string;
  name: string;
  photo_url: string;
  stock_total: number;
  stock_fbo: number;
  stock_fbs: number;
  avg_stock: number;
  orders_count: number;
  avg_daily_orders: number;
  /** Days the current stock lasts at the current rate. What the alert watches. */
  days_of_cover: number | null;
  /** Classic turnover over the window; only as honest as stock_days. */
  turnover_days: number | null;
  stock_days: number;
  status: TurnoverStatus;
}

export interface TurnoverArticles {
  seller_id: string;
  date: string | null;
  threshold_days: number;
  /** Окно, за которое считался темп продаж. Подписывает колонки таблицы. */
  window_days: number;
  articles: ArticleTurnover[];
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
