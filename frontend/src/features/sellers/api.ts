import { apiRequest } from "../../api/http";
import type { Seller, SellerArticle, SellerInput } from "./types";

export function getSellers(includeArchived = false) {
  const query = includeArchived ? "?include_archived=true" : "";
  return apiRequest<Seller[]>(`/api/v1/wb/sellers${query}`);
}

export const getSellerArticles = (sellerId: string) =>
  apiRequest<SellerArticle[]>(`/api/v1/wb/sellers/${sellerId}/articles`);

export const createSeller = (payload: SellerInput) =>
  apiRequest<Seller>("/api/v1/wb/sellers", { method: "POST", body: JSON.stringify(payload) });

export const updateSeller = (sellerId: string, payload: Partial<SellerInput>) =>
  apiRequest<Seller>(`/api/v1/wb/sellers/${sellerId}`, { method: "PATCH", body: JSON.stringify(payload) });

/** Retire the seller: he leaves every automation, everything collected stays. */
export const archiveSeller = (sellerId: string) =>
  apiRequest<void>(`/api/v1/wb/sellers/${sellerId}`, { method: "DELETE" });

/** Erase the seller together with the history of every automation. */
export const purgeSeller = (sellerId: string) =>
  apiRequest<void>(`/api/v1/wb/sellers/${sellerId}?purge=true`, { method: "DELETE" });

export const restoreSeller = (sellerId: string, apiKey: string) =>
  apiRequest<Seller>(`/api/v1/wb/sellers/${sellerId}/restore`, {
    method: "POST",
    body: JSON.stringify({ api_key: apiKey }),
  });

export const retrySellerSync = (sellerId: string) =>
  apiRequest<Seller>(`/api/v1/wb/sellers/${sellerId}/catalog-sync`, { method: "POST" });

/** Перепроверить ключ на шлюзе wb-egress (после починки прав в кабинете WB). */
export const verifySellerEgress = (sellerId: string) =>
  apiRequest<Seller>(`/api/v1/wb/sellers/${sellerId}/egress-verify`, { method: "POST" });

export const getAutomationSellers = (automationId: string) =>
  apiRequest<Seller[]>(`/api/v1/automations/${automationId}/sellers`);

export const attachSeller = (automationId: string, payload: { seller_id: string } | SellerInput) =>
  apiRequest<Seller>(`/api/v1/automations/${automationId}/sellers`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

/** Leave the automation. The seller and his data both stay. */
export const detachSeller = (automationId: string, sellerId: string) =>
  apiRequest<void>(`/api/v1/automations/${automationId}/sellers/${sellerId}`, { method: "DELETE" });
