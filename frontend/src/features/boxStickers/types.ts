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
  /** Страница исходного PDF, с которой взят стикер. */
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
