import { apiDownload, apiRequest } from "../../api/http";
import type { GroupKind, RefreshState, StocksBoard, StocksGroup, StocksSetup } from "./types";

const root = "/api/v1/wb/fbs-stocks";
const base = (sellerId: string) => `${root}/sellers/${sellerId}`;

export const getBoard = (sellerId: string) => apiRequest<StocksBoard>(base(sellerId));

/** Таблицы всех подключённых кабинетов — для сравнения. */
export const getBoards = () => apiRequest<StocksBoard[]>(`${root}/boards`);

export const getSetup = (sellerId: string) => apiRequest<StocksSetup>(`${base(sellerId)}/setup`);

export const addGroup = (sellerId: string, payload: { title: string; kind: GroupKind }) =>
  apiRequest<StocksGroup>(`${base(sellerId)}/groups`, { method: "POST", body: JSON.stringify(payload) });

export const updateGroup = (sellerId: string, groupId: string, payload: { title: string; kind: GroupKind }) =>
  apiRequest<void>(`${base(sellerId)}/groups/${groupId}`, { method: "PUT", body: JSON.stringify(payload) });

export const deleteGroup = (sellerId: string, groupId: string) =>
  apiRequest<void>(`${base(sellerId)}/groups/${groupId}`, { method: "DELETE" });

export const reorderGroups = (sellerId: string, groupIds: string[]) =>
  apiRequest<void>(`${base(sellerId)}/groups/order`, { method: "PUT", body: JSON.stringify({ group_ids: groupIds }) });

/** Состав группы целиком и в этом порядке; склад уходит из прежней группы сам. */
export const setGroupColumns = (sellerId: string, groupId: string, warehouseIds: number[]) =>
  apiRequest<void>(`${base(sellerId)}/groups/${groupId}/columns`, {
    method: "PUT",
    body: JSON.stringify({ warehouse_ids: warehouseIds }),
  });

/** Вставленный текст: по баркоду в строке, через запятую или пробел. */
export const addBarcodes = (sellerId: string, text: string) =>
  apiRequest<{ added: number }>(`${base(sellerId)}/barcodes`, { method: "POST", body: JSON.stringify({ text }) });

export const removeBarcode = (sellerId: string, barcode: string) =>
  apiRequest<void>(`${base(sellerId)}/barcodes/${encodeURIComponent(barcode)}`, { method: "DELETE" });

export const setNote = (sellerId: string, barcode: string, note: string) =>
  apiRequest<void>(`${base(sellerId)}/barcodes/${encodeURIComponent(barcode)}/note`, {
    method: "PUT",
    body: JSON.stringify({ note }),
  });

export const requestRefresh = (sellerId: string) =>
  apiRequest<RefreshState>(`${base(sellerId)}/refresh`, { method: "POST" });

export const requestRefreshAll = () => apiRequest<{ queued: number }>(`${root}/refresh`, { method: "POST" });

export const getRefreshState = (sellerId: string) => apiRequest<RefreshState | null>(`${base(sellerId)}/refresh`);

/** Книга в формате таблицы селлера: лист на кабинет и лист «Сравнение». */
export const downloadBoard = () => apiDownload(`${root}/export`);
