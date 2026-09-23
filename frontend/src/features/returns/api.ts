import { apiRequest } from "../../api/http";
import type { ExtensionState, InviteLink, PairingCode, RefreshState, Returns } from "./types";

const root = "/api/v1/wb/returns";
const base = (sellerId: string) => `${root}/sellers/${sellerId}`;

export const getReturns = (sellerId: string) => apiRequest<Returns>(base(sellerId));

/** Одноразовая ссылка на бота возвратов для этого кабинета. Выпуск новой гасит прежнюю. */
export const createInviteLink = (sellerId: string) =>
  apiRequest<InviteLink>(`${base(sellerId)}/invite-link`, { method: "POST" });

export const requestRefresh = (sellerId: string) =>
  apiRequest<RefreshState>(`${base(sellerId)}/refresh`, { method: "POST" });

export const getRefreshState = (sellerId: string) => apiRequest<RefreshState | null>(`${base(sellerId)}/refresh`);

export const getExtensionState = (sellerId: string) => apiRequest<ExtensionState>(`${base(sellerId)}/extension`);

/** Код на 15 минут для настроек расширения; новый гасит прежний, выпущенный со страницы. */
export const createPairingCode = (sellerId: string) =>
  apiRequest<PairingCode>(`${base(sellerId)}/extension/pairing-code`, { method: "POST" });

export const revokeInstall = (sellerId: string, installId: string) =>
  apiRequest<void>(`${base(sellerId)}/extension/installs/${installId}`, { method: "DELETE" });
