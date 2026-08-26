export type DistributionMode = "dry_run" | "write";

export interface SellerDistributionOverview {
  seller_id: string;
  mode: DistributionMode;
  write_enabled: boolean;
  enrolled_at: string;
}
