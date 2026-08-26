import { apiRequest } from "../../api/http";
import type {
  FbsQueueEntry,
  FbsSetup,
  MirrorSyncResult,
  SellerDistributionOverview,
} from "./types";

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

export const getSetup = () => apiRequest<FbsSetup>("/api/v1/wb/fbs/setup");

/** The order of the list is the priority order of the directions. */
export const saveRegions = (regions: { code: string; share_bp: number }[]) =>
  apiRequest<FbsSetup>("/api/v1/wb/fbs/setup/regions", {
    method: "PUT",
    body: JSON.stringify({ regions }),
  });

export const assignOffice = (officeId: number, regionCode: string | null) =>
  apiRequest<FbsSetup>(`/api/v1/wb/fbs/setup/offices/${officeId}`, {
    method: "PUT",
    body: JSON.stringify({ region_code: regionCode }),
  });

export const saveSettings = (payload: { reserve_units: number; priority_regions: number }) =>
  apiRequest<FbsSetup>("/api/v1/wb/fbs/setup/settings", { method: "PUT", body: JSON.stringify(payload) });

export const getQueue = (sellerId: string) =>
  apiRequest<FbsQueueEntry[]>(`/api/v1/wb/fbs/sellers/${sellerId}/queue`);

export const setPlacement = (
  sellerId: string,
  warehouseId: number,
  payload: { participates: boolean; position: number },
) =>
  apiRequest<FbsQueueEntry[]>(`/api/v1/wb/fbs/sellers/${sellerId}/warehouses/${warehouseId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
