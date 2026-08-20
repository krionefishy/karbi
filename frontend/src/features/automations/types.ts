export type AutomationStatus = "active" | "degraded" | "failed" | "running" | "idle";

export interface AutomationRun {
  id: string;
  trigger: "scheduled" | "manual";
  /** Which daily step ran, for automations that have several. */
  kind?: string | null;
  status: "queued" | "running" | "success" | "partial_success" | "error";
  snapshot_date: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  total_sellers: number;
  completed_sellers: number;
  failed_sellers: number;
  duration_seconds: number | null;
}

export interface Automation {
  id: string;
  title: string;
  description: string;
  status: AutomationStatus;
  seller_count: number;
  runs_last_24h: number;
  last_run: AutomationRun | null;
  last_success_at: string | null;
  next_run_at: string | null;
}
