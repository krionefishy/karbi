export type Granularity = "week" | "month";

/** total — итог, subtotal — подытог, item — статья расходов, info — справочная строка. */
export type LineLevel = "total" | "subtotal" | "item" | "info";

export interface PnlLine {
  key: string;
  title: string;
  level: LineLevel;
}

/** Строка отчёта -> сумма в рублях. */
export type PnlValues = Record<string, number>;

export interface PnlSellerState {
  seller_id: string;
  name: string;
  reports: number;
  /** Отчёты WB за год, строки которых ещё не дочитаны: их денег в цифрах нет. */
  pending_reports: number;
  collected_at: string | null;
  /** Когда воркер в последний раз складывал отчёты кабинета; null — ещё ни разу. */
  built_at: string | null;
  error: string | null;
}

export interface PnlPeriod {
  key: string;
  label: string;
  date_from: string;
  date_to: string;
  values: PnlValues;
  by_seller: Record<string, PnlValues>;
  pending_sellers: string[];
  /** Выручка до СПП по артикулам без себестоимости. */
  uncosted: number;
}

export interface UncostedArticle {
  seller_id: string;
  seller_name: string;
  nm_id: number;
  vendor_code: string;
  revenue: number;
}

export interface Pnl {
  year: number;
  granularity: Granularity;
  lines: PnlLine[];
  sellers: PnlSellerState[];
  periods: PnlPeriod[];
  total: PnlValues;
  total_by_seller: Record<string, PnlValues>;
  uncosted: UncostedArticle[];
}

export interface CostUploadResult {
  marketplace: "wb" | "ozon";
  added: number;
  changed: number;
  unchanged: number;
  unknown_cabinets: string[];
  problems: string[];
  effective_from: string;
}
