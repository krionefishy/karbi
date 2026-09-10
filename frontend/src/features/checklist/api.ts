import { apiDownload, apiRequest } from "../../api/http";
import type { Checklist, RefreshState } from "./types";

const base = (sellerId: string) => `/api/v1/wb/card-checklist/sellers/${sellerId}`;
const article = (sellerId: string, value: string) => `${base(sellerId)}/articles/${encodeURIComponent(value)}`;

export const getChecklist = (sellerId: string) => apiRequest<Checklist>(base(sellerId));

export const setMark = (sellerId: string, articleId: string, item: string, checked: boolean) =>
  apiRequest<void>(`${article(sellerId, articleId)}/items/${item}`, {
    method: "PUT",
    body: JSON.stringify({ checked }),
  });

export const setComment = (sellerId: string, articleId: string, text: string) =>
  apiRequest<void>(`${article(sellerId, articleId)}/comment`, {
    method: "PUT",
    body: JSON.stringify({ text }),
  });

/** Queues an out-of-schedule collection; pressing twice returns the same request. */
export const requestRefresh = (sellerId: string) =>
  apiRequest<RefreshState>(`${base(sellerId)}/refresh`, { method: "POST" });

export const getRefreshState = (sellerId: string) => apiRequest<RefreshState | null>(`${base(sellerId)}/refresh`);

/** Таблица в формате ручного чек-листа: те же колонки, TRUE/FALSE и формулы. */
export const downloadChecklist = (sellerId: string) => apiDownload(`${base(sellerId)}/export`);
