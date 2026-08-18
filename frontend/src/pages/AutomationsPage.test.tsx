import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { Automation } from "../features/automations/types";
import { AutomationsPage } from "./AutomationsPage";

vi.mock("../components/AppHeader", () => ({ AppHeader: () => null }));

const automation: Automation = {
  id: "wb-reviews",
  title: "Мониторинг отзывов Wildberries",
  description: "Ежедневные снимки отзывов.",
  status: "degraded",
  seller_count: 4,
  runs_last_24h: 2,
  last_run: {
    id: "5f2f0f5e-0000-4000-8000-000000000000",
    trigger: "scheduled",
    status: "partial_success",
    snapshot_date: "2026-08-18",
    created_at: "2026-08-18T09:00:00+00:00",
    started_at: "2026-08-18T09:00:00+00:00",
    finished_at: "2026-08-18T09:04:30+00:00",
    total_sellers: 4,
    completed_sellers: 3,
    failed_sellers: 1,
    duration_seconds: 270,
  },
  last_success_at: "2026-08-17T09:02:00+00:00",
  next_run_at: "2026-08-19T12:00:00+03:00",
};

function renderPage(data: Automation[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url.includes("/automations")
        ? new Response(JSON.stringify(data), { status: 200, headers: { "Content-Type": "application/json" } })
        : new Response(JSON.stringify({ database: true, redis: true }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AutomationsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AutomationsPage", () => {
  it("reports the real outcome of the last run", async () => {
    renderPage([automation]);

    expect(await screen.findByText("Прошла с ошибками")).toBeInTheDocument();
    expect(screen.getByText(/3 из 4/)).toBeInTheDocument();
    expect(screen.getByText(/1 с ошибкой/)).toBeInTheDocument();
    expect(screen.getByText(/4 мин 30 с/)).toBeInTheDocument();
    expect(screen.getByText(/за сутки запусков: 2/)).toBeInTheDocument();
  });

  it("says so plainly when the automation has never run", async () => {
    renderPage([{ ...automation, status: "idle", last_run: null, last_success_at: null, runs_last_24h: 0 }]);

    expect(await screen.findByText("Ещё не запускалась")).toBeInTheDocument();
    expect(screen.getByText("ещё не запускалась")).toBeInTheDocument();
    expect(screen.getByText("ни разу")).toBeInTheDocument();
  });
});
