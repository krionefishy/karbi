import { apiRequest } from "../../api/http";
import type { Seller, SellerReviewHistory } from "./types";

export function getSellers() {
  return apiRequest<Seller[]>("/api/v1/wb-reviews/sellers");
}

export function getSellerReviewHistory(sellerId: string) {
  return apiRequest<SellerReviewHistory>(`/api/v1/wb-reviews/sellers/${sellerId}/history`);
}
