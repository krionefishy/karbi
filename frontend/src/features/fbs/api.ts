import { apiRequest } from "../../api/http";
import type { MirrorSyncResult, SellerDistributionOverview } from "./types";

export const getSellerOverview = (sellerId: string) =>
  apiRequest<SellerDistributionOverview>(`/api/v1/wb/fbs/sellers/${sellerId}/overview`);

/** Allow or forbid the automation to rewrite this cabinet's stock in WB. */
export const setWriteEnabled = (sellerId: string, writeEnabled: boolean) =>
  apiRequest<SellerDistributionOverview>(`/api/v1/wb/fbs/sellers/${sellerId}/mode`, {
    method: "PUT",
    body: JSON.stringify({ write_enabled: writeEnabled }),
  });

/** Re-read the WB office catalogue and the cabinet's warehouses right now. */
export const syncMirror = (sellerId: string) =>
  apiRequest<MirrorSyncResult>(`/api/v1/wb/fbs/sellers/${sellerId}/sync`, { method: "POST" });
