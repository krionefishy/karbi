import { apiRequest } from "../../api/http";
import type { InviteLink, RefreshState, Returns } from "./types";

const root = "/api/v1/wb/returns";
const base = (sellerId: string) => `${root}/sellers/${sellerId}`;

export const getReturns = (sellerId: string) => apiRequest<Returns>(base(sellerId));

/** Одноразовая ссылка на бота возвратов для этого кабинета. Выпуск новой гасит прежнюю. */
export const createInviteLink = (sellerId: string) =>
  apiRequest<InviteLink>(`${base(sellerId)}/invite-link`, { method: "POST" });

export const requestRefresh = (sellerId: string) =>
  apiRequest<RefreshState>(`${base(sellerId)}/refresh`, { method: "POST" });

export const getRefreshState = (sellerId: string) => apiRequest<RefreshState | null>(`${base(sellerId)}/refresh`);
