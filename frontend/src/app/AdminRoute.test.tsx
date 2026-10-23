import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../features/auth/AuthContext";
import { AppRoutes } from "./App";

vi.mock("../pages/AutomationsPage", () => ({ AutomationsPage: () => <div>каталог автоматизаций</div> }));
vi.mock("../pages/admin/ScreenshotsPage", () => ({ ScreenshotsPage: () => <div>админка скриншотов</div> }));

const { restoreSession } = vi.hoisted(() => ({ restoreSession: vi.fn() }));
vi.mock("../features/auth/api", () => ({ restoreSession, logout: vi.fn() }));

function open(isAdmin: boolean) {
  restoreSession.mockResolvedValue({ access_token: "t", user: { id: "1", username: "lera", is_admin: isAdmin } });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/admin/screenshots"]}>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("/admin/screenshots", () => {
  it("opens for an admin", async () => {
    open(true);
    expect(await screen.findByText("админка скриншотов")).toBeInTheDocument();
  });

  it("sends a regular employee back to the catalog", async () => {
    open(false);
    expect(await screen.findByText("каталог автоматизаций")).toBeInTheDocument();
    expect(screen.queryByText("админка скриншотов")).not.toBeInTheDocument();
  });
});
