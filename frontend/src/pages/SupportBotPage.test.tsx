import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getSupportBot, setMode, setPaused } from "../features/supportBot/api";
import type { SupportBot } from "../features/supportBot/types";
import { SupportBotPage } from "./SupportBotPage";

vi.mock("../components/Shell", () => ({ Shell: ({ children }: { children: ReactNode }) => <>{children}</> }));
vi.mock("../components/SellerSwitcher", () => ({ SellerSwitcher: () => null }));
vi.mock("../components/SupportBotProducts", () => ({ SupportBotProducts: () => null }));
vi.mock("../components/SupportBotReplies", () => ({ SupportBotReplies: () => null }));
vi.mock("../components/SupportBotMonitor", () => ({ SupportBotMonitor: () => null }));
vi.mock("../features/sellers/api", () => ({
  getAutomationSellers: vi.fn().mockResolvedValue([{ id: "saniev", name: "ИП Саниев" }]),
  getSellers: vi.fn().mockResolvedValue([]),
  attachSeller: vi.fn(), detachSeller: vi.fn(), retrySellerSync: vi.fn(),
}));
vi.mock("../features/supportBot/api", () => ({
  getSupportBot: vi.fn(),
  getSends: vi.fn().mockResolvedValue({ sends: [], total: 0, counts: {}, page: 1, page_size: 50 }),
  setMode: vi.fn().mockResolvedValue(undefined), setPaused: vi.fn(),
  savePace: vi.fn(), addTemplate: vi.fn(), deleteTemplate: vi.fn(),
  setTemplateActive: vi.fn(), updateTemplate: vi.fn(),
}));

const pace = { interval_minutes: 60, jitter_min_minutes: 20, jitter_max_minutes: 25 };
const stoppedByError: SupportBot = {
  seller_id: "saniev", seller_name: "ИП Саниев", mode: "live",
  paused_reason: "WB Buyers Chat API: отказ доступа (HTTP 403)", globally_paused: false,
  counts: {}, templates: [{ id: 1, text: "Здравствуйте!", active: true }],
  mirror_error: null, mirror_read_through: null, classifier_mode: "enforce",
  reply_gate_mode: "enforce", monitor_overall: "warn", pace, pace_default: pace, pace_custom: true,
};

function setup(view = stoppedByError) {
  vi.mocked(getSupportBot).mockResolvedValue(view);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><SupportBotPage /></QueryClientProvider>);
}

beforeEach(() => vi.clearAllMocks());

describe("возобновление рассылки после отказа WB", () => {
  it("позволяет снять остановку кабинета только после подтверждения, сохраняя общую паузу", async () => {
    setup({ ...stoppedByError, globally_paused: true });
    await userEvent.click(await screen.findByRole("button", { name: "Возобновить рассылку" }));
    expect(setMode).not.toHaveBeenCalled();
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByRole("heading", { name: "Возобновить отправку по кабинету?" })).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Возобновить" }));
    await waitFor(() => expect(setMode).toHaveBeenCalledWith("saniev", "live"));
    expect(setPaused).not.toHaveBeenCalled();
  });

  it("не разрешает возобновление без активного текста", async () => {
    setup({ ...stoppedByError, templates: [] });
    expect(await screen.findByRole("button", { name: "Возобновить рассылку" })).toBeDisabled();
    expect(setMode).not.toHaveBeenCalled();
  });

  it("не предлагает возобновление уже работающему кабинету", async () => {
    setup({ ...stoppedByError, paused_reason: null });
    await screen.findByText(/Диалоги ИП Саниев:/);
    expect(screen.queryByRole("button", { name: "Возобновить рассылку" })).not.toBeInTheDocument();
  });
});
