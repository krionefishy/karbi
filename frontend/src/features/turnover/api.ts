import { apiDownload, apiRequest } from "../../api/http";
import type { InviteLink, RefreshState, TurnoverArticles } from "./types";

export const getTurnoverArticles = (sellerId: string) =>
  apiRequest<TurnoverArticles>(`/api/v1/wb/turnover/sellers/${sellerId}/articles`);

export const createInviteLink = (sellerId: string) =>
  apiRequest<InviteLink>(`/api/v1/wb/turnover/sellers/${sellerId}/invite-link`, { method: "POST" });

export const requestRefresh = (sellerId: string) =>
  apiRequest<RefreshState>(`/api/v1/wb/turnover/sellers/${sellerId}/refresh`, { method: "POST" });

export const getRefreshState = (sellerId: string) =>
  apiRequest<RefreshState | null>(`/api/v1/wb/turnover/sellers/${sellerId}/refresh`);

export const downloadReplenishmentReport = (sellerId: string) =>
  apiDownload(`/api/v1/wb/turnover/sellers/${sellerId}/replenishment`);
