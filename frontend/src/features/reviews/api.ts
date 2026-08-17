import { apiRequest } from "../../api/http";
import type { ReviewSyncRun, Seller, SellerArticle, SellerInput, SellerReviewHistory } from "./types";

export function getSellers() {
  return apiRequest<Seller[]>("/api/v1/wb/sellers");
}

export const getSellerArticles = (sellerId: string) =>
  apiRequest<SellerArticle[]>(`/api/v1/wb/sellers/${sellerId}/articles`);

export const createSeller = (payload: SellerInput) =>
  apiRequest<Seller>("/api/v1/wb/sellers", { method: "POST", body: JSON.stringify(payload) });

export const updateSeller = (sellerId: string, payload: Partial<SellerInput>) =>
  apiRequest<Seller>(`/api/v1/wb/sellers/${sellerId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

export const deleteSeller = (sellerId: string) =>
  apiRequest<void>(`/api/v1/wb/sellers/${sellerId}`, { method: "DELETE" });

export const retrySellerSync = (sellerId: string) =>
  apiRequest<Seller>(`/api/v1/wb/sellers/${sellerId}/catalog-sync`, { method: "POST" });

export const getSellerReviewHistory = (sellerId: string, days = 90) =>
  apiRequest<SellerReviewHistory>(`/api/v1/wb/reviews/sellers/${sellerId}/history?days=${days}`);

export const getLatestReviewSync = () =>
  apiRequest<ReviewSyncRun | null>("/api/v1/wb/reviews/sync/latest");

export const startReviewSync = () =>
  apiRequest<ReviewSyncRun>("/api/v1/wb/reviews/sync", { method: "POST" });
