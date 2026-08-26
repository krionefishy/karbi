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
