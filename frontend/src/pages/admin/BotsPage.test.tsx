import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { NotificationBot } from "../../features/admin/types";
import { BotsPage } from "./BotsPage";

vi.mock("../../components/AdminHeader", () => ({ AdminHeader: () => null }));

const { getBots, createBot, deleteBot } = vi.hoisted(() => ({
  getBots: vi.fn(),
  createBot: vi.fn(),
  deleteBot: vi.fn(),
}));

vi.mock("../../features/admin/api", () => ({ getBots, createBot, deleteBot }));

const bot: NotificationBot = {
  id: "9c3a0f5e-0000-4000-8000-000000000000",
  code: "turnover-alerts",
  title: "Оборачиваемость",
  invite_link_template: "https://t.me/mplace_auto_bot?start={token}",
};

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <BotsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("BotsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getBots.mockResolvedValue([bot]);
    createBot.mockResolvedValue(bot);
    deleteBot.mockResolvedValue(undefined);
  });

  it("shows the invite template without pretending it is a usable link", async () => {
    renderPage();

    // The token placeholder is what makes it a template, not a link to click.
    expect(await screen.findByText("https://t.me/mplace_auto_bot?start=…")).toBeInTheDocument();
    expect(screen.getByText("turnover-alerts")).toBeInTheDocument();
  });

  it("says plainly when nothing is connected", async () => {
    getBots.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText(/Ботов пока нет/)).toBeInTheDocument();
  });

  it("sends the token typed into the dialog", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("turnover-alerts");

    await user.click(screen.getByRole("button", { name: "Подключить бота" }));
    await user.type(screen.getByPlaceholderText("turnover-alerts"), "reviews-alerts");
    await user.type(screen.getByPlaceholderText("123456789:AA..."), "1234:secret");
    await user.click(screen.getByRole("button", { name: "Подключить" }));

    // Only the first argument matters: react-query adds its own second one.
    await waitFor(() => expect(createBot).toHaveBeenCalled());
    expect(createBot.mock.calls[0][0]).toEqual({ code: "reviews-alerts", title: "", token: "1234:secret" });
  });

  it("asks before disconnecting, because subscriptions go with it", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();
    await screen.findByText("turnover-alerts");

    await user.click(screen.getByRole("button", { name: "Отключить turnover-alerts" }));

    expect(window.confirm).toHaveBeenCalled();
    expect(deleteBot).not.toHaveBeenCalled();
  });
});
