import { apiRequest } from "../../api/http";
import type { InviteLink, RefreshState, TurnoverArticles } from "./types";

export const getTurnoverArticles = (sellerId: string) =>
  apiRequest<TurnoverArticles>(`/api/v1/wb/turnover/sellers/${sellerId}/articles`);

/** Issues a fresh personal link to the bot; the previous one stops working. */
export const createInviteLink = (sellerId: string) =>
  apiRequest<InviteLink>(`/api/v1/wb/turnover/sellers/${sellerId}/invite-link`, { method: "POST" });

/** Queues an out-of-schedule collection; pressing twice returns the same request. */
export const requestRefresh = (sellerId: string) =>
  apiRequest<RefreshState>(`/api/v1/wb/turnover/sellers/${sellerId}/refresh`, { method: "POST" });

export const getRefreshState = (sellerId: string) =>
  apiRequest<RefreshState | null>(`/api/v1/wb/turnover/sellers/${sellerId}/refresh`);
