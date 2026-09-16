import { apiDownload, apiRequest } from "../../api/http";
import type { Lookup, Penalties, PenaltiesFilter, RefreshState } from "./types";

const root = "/api/v1/wb/fbs-penalties";
const base = (sellerId: string) => `${root}/sellers/${sellerId}`;

function query(filter: PenaltiesFilter): string {
  const params = new URLSearchParams({ date_from: filter.dateFrom, date_to: filter.dateTo });
  if (filter.group) params.set("group", filter.group);
  if (filter.warehouseId !== null) params.set("warehouse", String(filter.warehouseId));
  return params.toString();
}

export const PAGE_SIZE = 200;

export const getPenalties = (sellerId: string, filter: PenaltiesFilter) =>
  apiRequest<Penalties>(`${base(sellerId)}?${query(filter)}&page=${filter.page}&page_size=${PAGE_SIZE}`);

/** Вставленный текст: стикеры или номера заказов — по одному в строке, через запятую или пробел. */
export const lookupPenalties = (sellerId: string, text: string) =>
  apiRequest<Lookup>(`${base(sellerId)}/lookup`, { method: "POST", body: JSON.stringify({ keys: [text] }) });

export const downloadPenalties = (sellerId: string, filter: PenaltiesFilter) =>
  apiDownload(`${base(sellerId)}/export?${query({ ...filter, group: "", warehouseId: null, page: 1 })}`);

export const requestRefresh = (sellerId: string) =>
  apiRequest<RefreshState>(`${base(sellerId)}/refresh`, { method: "POST" });

export const getRefreshState = (sellerId: string) => apiRequest<RefreshState | null>(`${base(sellerId)}/refresh`);
