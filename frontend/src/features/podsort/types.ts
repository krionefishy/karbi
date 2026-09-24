export interface PodsortSettings {
  window_days: number;
  cover_days: number;
  regions: string[];
  window_choices: number[];
  max_cover_days: number;
  available_regions: string[];
}

export interface PodsortSettingsInput {
  window_days: number;
  cover_days: number;
  regions: string[];
}

export interface PodsortSellerState {
  seller_id: string;
  name: string;
  window_days_loaded: number;
  history_from: string | null;
  history_days_loaded: number;
  collected_at: string | null;
  collection_error: string | null;
  remains_at: string | null;
  /** Снимок остатков давно не обновлялся — вычитается устаревший остаток. */
  remains_stale: boolean;
  remains_error: string | null;
}

/** manual — регион задал человек, guess — угадан по городу, unplaced — WB не говорит где, none — неизвестно. */
export type WarehouseSource = "manual" | "guess" | "unplaced" | "none";

export interface PodsortWarehouse {
  name: string;
  region: string | null;
  source: WarehouseSource;
  quantity: number;
}

export interface PodsortRow {
  seller_name: string;
  nm_id: number;
  barcode: string;
  vendor_code: string;
  subject: string;
  tech_size: string;
  need: number;
  window_orders: number;
  average: number;
  stock: number;
  cover_days: number | null;
}

export interface Podsort {
  today: string;
  last_day: string;
  window_start: string;
  settings: PodsortSettings;
  sellers: PodsortSellerState[];
  warehouses: PodsortWarehouse[];
  region: string;
  rows: PodsortRow[];
}
