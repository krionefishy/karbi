export interface BoxStickerItem {
  barcode: string;
  quantity: number;
  vendor_code: string;
  tech_size: string;
}

export interface BoxStickerBox {
  position: number;
  shk: string;
  package_code: string;
  page: number;
  quantity: number;
  items: BoxStickerItem[];
}

export interface BoxStickerPlan {
  supply_id: number | null;
  seller_id: string | null;
  seller_name: string;
  ready: boolean;
  problems: string[];
  boxes: BoxStickerBox[];
}

export interface PackedBox {
  position: number;
  shk: string;
  package_code: string;
  barcode: string;
  quantity: number;
}

export interface PackingPlan {
  supply_id: number | null;
  seller_id: string | null;
  seller_name: string;
  ready: boolean;
  problems: string[];
  boxes: PackedBox[];
}

export interface RecentPacking {
  supply_id: number;
  seller_name: string;
  boxes: number;
  saved_at: string;
}

export type WorkbookSource = { kind: "file"; file: File } | { kind: "supply"; supplyId: number };
