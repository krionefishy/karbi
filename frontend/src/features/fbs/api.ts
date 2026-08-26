import { apiRequest } from "../../api/http";
import type { SellerDistributionOverview } from "./types";

export const getSellerOverview = (sellerId: string) =>
  apiRequest<SellerDistributionOverview>(`/api/v1/wb/fbs/sellers/${sellerId}/overview`);

/** Allow or forbid the automation to rewrite this cabinet's stock in WB. */
export const setWriteEnabled = (sellerId: string, writeEnabled: boolean) =>
  apiRequest<SellerDistributionOverview>(`/api/v1/wb/fbs/sellers/${sellerId}/mode`, {
    method: "PUT",
    body: JSON.stringify({ write_enabled: writeEnabled }),
  });
