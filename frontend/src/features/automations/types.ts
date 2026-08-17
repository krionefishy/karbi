export interface Automation {
  id: string;
  title: string;
  description: string;
  status: "active" | "coming_soon";
  last_run_at: string | null;
  seller_count: number | null;
}
