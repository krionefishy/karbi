import { apiDownload, apiRequest } from "../../api/http";

export type BoardTone = "critical" | "warning" | "calm" | "neutral";
export type BoardStatus = "ok" | "no_sales" | "no_data";
export type BoardSortKey = "days" | "orders" | "stock";

export interface BoardSeller {
  seller_id: string;
  name: string;
  stock_at: string | null;
  stock_stale: boolean;
  fbs_at: string | null;
  orders_without_barcode: number;
}

export interface BoardRow {
  seller_id: string;
  seller_name: string;
  barcode: string;
  article: string;
  vendor_code: string;
  name: string;
  nomenclature: string;
  note: string;
  in_catalog: boolean;
  stock: number | null;
  orders: number;
  rate: number;
  days: number | null;
  status: BoardStatus;
  tone: BoardTone;
  fbs: number | null;
}

export interface BoardHiddenRow {
  seller_id: string;
  seller_name: string;
  barcode: string;
  note: string;
  name: string;
  hidden_at: string;
}

export interface Board {
  date: string;
  window_from: string;
  window_to: string;
  window_days: number;
  red_days: number;
  yellow_days: number;
  sellers: BoardSeller[];
  rows: BoardRow[];
  hidden: BoardHiddenRow[];
}

const root = "/api/v1/wb/turnover/board";
const item = (sellerId: string, barcode: string) =>
  `${root}/sellers/${sellerId}/items/${encodeURIComponent(barcode)}`;

export const getBoard = () => apiRequest<Board>(root);

export const addBoardItems = (sellerId: string, text: string) =>
  apiRequest<{ added: number; existing: number }>(`${root}/sellers/${sellerId}/items`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });

export const updateBoardItem = (sellerId: string, barcode: string, patch: { note?: string; nomenclature?: string }) =>
  apiRequest<void>(item(sellerId, barcode), { method: "PATCH", body: JSON.stringify(patch) });

export const setBoardItemHidden = (sellerId: string, barcode: string, hidden: boolean) =>
  apiRequest<void>(`${item(sellerId, barcode)}/hidden`, { method: "PUT", body: JSON.stringify({ hidden }) });

export const removeBoardItem = (sellerId: string, barcode: string) =>
  apiRequest<void>(item(sellerId, barcode), { method: "DELETE" });

export interface ImportCabinet {
  name: string;
  seller_name: string | null;
  connected_now: boolean;
  added: number;
  existing: number;
  unknown: number;
}

export const importBoard = (workbook: File) => {
  const body = new FormData();
  body.append("workbook", workbook);
  return apiRequest<{ cabinets: ImportCabinet[] }>(`${root}/import`, { method: "POST", body });
};

export function importSummary(cabinets: ImportCabinet[]): string {
  const parts = cabinets.map((cabinet) => {
    if (!cabinet.seller_name) return `«${cabinet.name}» — такого кабинета нет в реестре, пропущен`;
    const connected = cabinet.connected_now ? ", кабинет подключён" : "";
    const unknown = cabinet.unknown > 0 ? `, ${cabinet.unknown} баркодов не найдено в каталоге` : "";
    return `${cabinet.seller_name}: добавлено ${cabinet.added}, уже были ${cabinet.existing}${connected}${unknown}`;
  });
  return `${parts.join("; ")}. Заказы и остатки соберутся в течение нескольких минут.`;
}

export interface FbsWarehouse {
  key: string;
  label: string;
  selected: boolean;
  cabinets: number;
}

export const getFbsWarehouses = () => apiRequest<FbsWarehouse[]>(`${root}/fbs-warehouses`);

export const setFbsWarehouses = (keys: string[]) =>
  apiRequest<FbsWarehouse[]>(`${root}/fbs-warehouses`, { method: "PUT", body: JSON.stringify({ keys }) });

export const refreshBoard = () => apiRequest<{ queued: number }>(`${root}/refresh`, { method: "POST" });

export const downloadBoard = () => apiDownload(`${root}/export`);

export function daysText(row: BoardRow): string {
  if (row.status === "no_sales") return "нет продаж";
  if (row.status === "no_data" || row.days === null) return "нет данных";
  return `${row.days} дн.`;
}

export const fbsText = (row: BoardRow): string => (row.fbs === null ? "—" : String(row.fbs));

export function visibleBoardRows(rows: BoardRow[], sellerId: string, search: string): BoardRow[] {
  const needle = search.trim().toLowerCase();
  return rows.filter((row) => {
    if (sellerId && row.seller_id !== sellerId) return false;
    if (!needle) return true;
    return [row.barcode, row.article, row.vendor_code, row.name, row.note, row.nomenclature].some((value) =>
      value.toLowerCase().includes(needle),
    );
  });
}

const sortValue = (row: BoardRow, key: BoardSortKey): number | null => {
  if (key === "days") return row.days;
  if (key === "orders") return row.orders;
  return row.stock;
};

export function sortBoardRows(rows: BoardRow[], key: BoardSortKey, direction: "asc" | "desc"): BoardRow[] {
  const sign = direction === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const left = sortValue(a, key);
    const right = sortValue(b, key);
    if (left === null && right === null) return a.barcode.localeCompare(b.barcode);
    if (left === null) return 1;
    if (right === null) return -1;
    return (left - right) * sign || a.barcode.localeCompare(b.barcode);
  });
}

export function boardNotices(board: Board, sellerId: string): string[] {
  const dateTime = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });
  const notices: string[] = [];
  for (const seller of board.sellers) {
    if (sellerId && seller.seller_id !== sellerId) continue;
    const hasRows = board.rows.some((row) => row.seller_id === seller.seller_id);
    if (!hasRows) continue;
    if (!seller.stock_at) {
      notices.push(`${seller.name}: остатки WB ещё не собирались — «Хватит на» показать нечем. Нажмите «Обновить данные».`);
    } else if (seller.stock_stale) {
      notices.push(`${seller.name}: остатки WB сняты ${dateTime.format(new Date(seller.stock_at))} и устарели.`);
    }
    if (seller.orders_without_barcode > 0) {
      notices.push(
        `${seller.name}: ${seller.orders_without_barcode} заказов за окно ещё без баркода — цифры занижены до ближайшего сбора.`,
      );
    }
  }
  return notices;
}
