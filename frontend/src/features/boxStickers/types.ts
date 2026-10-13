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

/** Короб шаблона WB, получивший товар из таблицы сборки. */
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

/** Коробовка, заполненная здесь: на шаге стикеров её Excel не нужен. */
export interface RecentPacking {
  supply_id: number;
  seller_name: string;
  boxes: number;
  saved_at: string;
}

/** Откуда брать Excel коробовки на шаге стикеров: файлом или по номеру поставки. */
export type WorkbookSource = { kind: "file"; file: File } | { kind: "supply"; supplyId: number };
