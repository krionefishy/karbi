export type DeductionGroup = "penalties" | "deductions" | "logistics" | "storage";

/** Как строка отчёта сошлась с заданием: есть поставка, задание без поставки, задания нет. */
export type TraceState = "found" | "no_supply" | "no_order";

export interface PenaltyRow {
  rrd_id: number;
  /** «2026-09-01/2026-09-07» — неделя отчёта, из которой строка. */
  report_period: string;
  barcode: string;
  nm_id: number;
  title: string;
  /** Вид удержания словами WB. */
  kind: string;
  group: DeductionGroup | "";
  group_title: string;
  amount: number;
  sticker_id: string;
  srid: string;
  assembly_id: string;
  order_dt: string | null;
  trace: TraceState;
  warehouse_name: string | null;
  warehouse_id: number | null;
  supply_id: string | null;
  supply_created_at: string | null;
  supply_scan_dt: string | null;
  destination_office_name: string | null;
}

export interface GroupTotal {
  group: DeductionGroup;
  title: string;
  count: number;
  amount: number;
}

export interface WarehouseOption {
  warehouse_id: number;
  name: string;
}

export interface Penalties {
  seller_id: string;
  seller_name: string;
  date_from: string;
  date_to: string;
  collected_at: string | null;
  collection_error: string | null;
  /** Одна страница строк; итоги — за весь период. */
  rows: PenaltyRow[];
  totals: GroupTotal[];
  warehouses: WarehouseOption[];
  page: number;
  page_size: number;
  total_rows: number;
}

export interface LookupMiss {
  key: string;
  reason: string;
}

export interface Lookup {
  rows: PenaltyRow[];
  missing: LookupMiss[];
}

export interface RefreshState {
  status: "queued" | "running" | "success" | "error";
  in_progress: boolean;
  requested_at: string;
  finished_at: string | null;
  error: string | null;
}

export interface PenaltiesFilter {
  dateFrom: string;
  dateTo: string;
  group: DeductionGroup | "";
  warehouseId: number | null;
  page: number;
}
