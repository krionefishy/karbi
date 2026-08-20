import { apiRequest } from "../../api/http";
import type { InviteLink, TurnoverArticles } from "./types";

export const getTurnoverArticles = (sellerId: string) =>
  apiRequest<TurnoverArticles>(`/api/v1/wb/turnover/sellers/${sellerId}/articles`);

/** Issues a fresh personal link to the bot; the previous one stops working. */
export const createInviteLink = (sellerId: string) =>
  apiRequest<InviteLink>(`/api/v1/wb/turnover/sellers/${sellerId}/invite-link`, { method: "POST" });
