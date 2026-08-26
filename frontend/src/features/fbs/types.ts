export type DistributionMode = "dry_run" | "write";

export interface FbsWarehouse {
  warehouse_id: number;
  office_id: number;
  name: string;
  city: string;
  address: string;
  federal_district: string;
  /** Тип груза объекта WB: 1 — обычный, 3 — КГТ. */
  cargo_type: number;
  is_processing: boolean;
  is_deleting: boolean;
  participates: boolean;
  position: number;
  region_code: string | null;
}

export interface SellerDistributionOverview {
  seller_id: string;
  mode: DistributionMode;
  write_enabled: boolean;
  enrolled_at: string;
  warehouses_synced_at: string | null;
  offices_known: number;
  warehouses: FbsWarehouse[];
}

export interface MirrorSyncResult {
  offices: number;
  warehouses: number;
}

export interface FbsRegion {
  code: string;
  title: string;
  position: number;
  /** Доля в сотых долях процента: 4000 — это 40%. */
  share_bp: number;
}

export interface FbsOffice {
  office_id: number;
  name: string;
  city: string;
  address: string;
  federal_district: string;
  cargo_type: number;
  region_code: string | null;
  used_by_cabinets: number;
}

export interface FbsSetup {
  regions: FbsRegion[];
  shares_ready: boolean;
  reserve_units: number;
  priority_regions: number;
  offices: FbsOffice[];
  unassigned_offices: number;
}

export interface FbsQueueEntry {
  place: number;
  warehouse_id: number;
  name: string;
  city: string;
  region_code: string | null;
  region_title: string;
}

export interface FbsStockState {
  snapshot_id: string | null;
  source: string;
  generated_at: string | null;
  received_at: string | null;
  lines: number;
  stale: boolean;
  pools: number;
  on_hand_total: number;
  available_total: number;
  reserve_units: number;
}

export interface FbsSnapshotRecord {
  id: string;
  source: string;
  generated_at: string;
  received_at: string;
  lines: number;
  status: "accepted" | "rejected";
  error: string | null;
}

export interface FbsPool {
  item_id: string;
  characteristic: string;
  barcode: string;
  name: string;
  on_hand: number;
  available: number;
}
