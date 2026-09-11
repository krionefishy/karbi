export type GroupKind = "own" | "fulfilment" | "district";

export interface StocksColumn {
  warehouse_id: number;
  name: string;
}

export interface StocksGroup {
  id: string;
  title: string;
  kind: GroupKind;
  columns: StocksColumn[];
}

export interface StocksRow {
  barcode: string;
  note: string;
  article: string;
  title: string;
  vendor_code: string;
  /** Баркода нет в каталоге кабинета — подсказать, что за товар, нечем. */
  in_catalog: boolean;
  /** Остаток по складу: ключ — id склада строкой. */
  amounts: Record<string, number>;
  /** Сумма по группе: ключ — id группы. */
  totals: Record<string, number>;
}

export interface StocksBoard {
  seller_id: string;
  seller_name: string;
  collected_at: string | null;
  collection_error: string | null;
  groups: StocksGroup[];
  rows: StocksRow[];
}

export interface WarehouseSetup {
  warehouse_id: number;
  name: string;
  delivery_type: number;
  is_deleting: boolean;
  group_id: string | null;
  position: number;
}

export interface StocksSetup {
  seller_id: string;
  groups: StocksGroup[];
  warehouses: WarehouseSetup[];
}

export interface RefreshState {
  status: "queued" | "running" | "success" | "error";
  in_progress: boolean;
  requested_at: string;
  finished_at: string | null;
  error: string | null;
}

export const GROUP_KIND_LABELS: Record<GroupKind, string> = {
  own: "Наш склад",
  fulfilment: "Фулфилмент",
  district: "Округ",
};
