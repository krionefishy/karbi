import { apiRequest } from "../../api/http";
import type { Seller, SellerArticle, SellerInput } from "./types";

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
