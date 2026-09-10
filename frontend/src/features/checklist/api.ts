import { apiDownload, apiRequest } from "../../api/http";
import type { Checklist, RefreshState } from "./types";

const base = (sellerId: string) => `/api/v1/wb/card-checklist/sellers/${sellerId}`;

export const getChecklist = (sellerId: string) => apiRequest<Checklist>(base(sellerId));

export const setComment = (sellerId: string, article: string, text: string) =>
  apiRequest<void>(`${base(sellerId)}/articles/${encodeURIComponent(article)}/comment`, {
    method: "PUT",
    body: JSON.stringify({ text }),
  });

/** Queues an out-of-schedule collection; pressing twice returns the same request. */
export const requestRefresh = (sellerId: string) =>
  apiRequest<RefreshState>(`${base(sellerId)}/refresh`, { method: "POST" });

export const getRefreshState = (sellerId: string) => apiRequest<RefreshState | null>(`${base(sellerId)}/refresh`);

/** Таблица в формате ручного чек-листа: реквизиты, данные WB и формулы «готово». */
export const downloadChecklist = (sellerId: string) => apiDownload(`${base(sellerId)}/export`);
