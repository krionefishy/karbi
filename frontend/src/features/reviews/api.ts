import { apiDownload, apiRequest } from "../../api/http";
import type { GroupAction, ReviewSyncRun, SellerReviewHistory } from "./types";

export const getSellerReviewHistory = (sellerId: string, days = 90) =>
  apiRequest<SellerReviewHistory>(`/api/v1/wb/reviews/sellers/${sellerId}/history?days=${days}`);

export const getLatestReviewSync = () =>
  apiRequest<ReviewSyncRun | null>("/api/v1/wb/reviews/sync/latest");

export const startReviewSync = () =>
  apiRequest<ReviewSyncRun>("/api/v1/wb/reviews/sync", { method: "POST" });

export const downloadReviewReport = (sellerId: string, dateFrom: string, dateTo: string) =>
  apiDownload(
    `/api/v1/wb/reviews/sellers/${sellerId}/report?date_from=${dateFrom}&date_to=${dateTo}`,
  );

export const setProductGroup = (
  sellerId: string,
  article: string,
  action: GroupAction,
  mainArticle: string | null = null,
) =>
  apiRequest<void>(`/api/v1/wb/reviews/sellers/${sellerId}/products/${encodeURIComponent(article)}/group`, {
    method: "PUT",
    body: JSON.stringify({ action, main_article: mainArticle }),
  });
