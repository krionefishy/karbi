import { ApiError, apiRequest } from "../../api/http";
import type { CardDocuments, DocumentState, InviteLink, RefreshState } from "./types";

const base = (sellerId: string) => `/api/v1/wb/card-documents/sellers/${encodeURIComponent(sellerId)}`;

export const getCardDocuments = (sellerId: string, state?: DocumentState) =>
  apiRequest<CardDocuments>(state ? `${base(sellerId)}?state=${state}` : base(sellerId));

export const requestRefresh = (sellerId: string) =>
  apiRequest<RefreshState>(`${base(sellerId)}/refresh`, { method: "POST" });

export const getRefreshState = (sellerId: string) => apiRequest<RefreshState | null>(`${base(sellerId)}/refresh`);

export const getInviteLink = (sellerId: string) =>
  apiRequest<InviteLink>(`${base(sellerId)}/invite-link`, { method: "POST" });

export function inviteErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 409) {
    return "Бот уведомлений о документах ещё не зарегистрирован — обратитесь к администратору.";
  }
  return error instanceof ApiError ? `Не удалось выпустить ссылку: ${error.message}` : "Не удалось выпустить ссылку";
}
