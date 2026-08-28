import { apiDownload, apiRequest } from "../../api/http";
import type { ReviewSyncRun, SellerReviewHistory } from "./types";

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
