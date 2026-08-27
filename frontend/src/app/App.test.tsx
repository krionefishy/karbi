import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AppRoutes } from "./App";

// Страницы подменены пустышками: проверяется маршрутизация, а не их содержимое.
vi.mock("../pages/AutomationsPage", () => ({ AutomationsPage: () => <div>каталог автоматизаций</div> }));
vi.mock("../pages/ReviewsPage", () => ({ ReviewsPage: () => <div>отзывы</div> }));
vi.mock("../pages/TurnoverPage", () => ({ TurnoverPage: () => <div>оборачиваемость</div> }));
vi.mock("../pages/FbsDistributionPage", () => ({ FbsDistributionPage: () => <div>распределение FBS</div> }));
vi.mock("../pages/SellersPage", () => ({ SellersPage: () => <div>селлеры</div> }));
vi.mock("../pages/LoginPage", () => ({ LoginPage: () => <div>вход</div> }));
vi.mock("../pages/admin/EmployeesPage", () => ({ EmployeesPage: () => <div>сотрудники</div> }));
vi.mock("../pages/admin/BotsPage", () => ({ BotsPage: () => <div>боты</div> }));

vi.mock("../features/auth/AuthContext", async () => {
  const { Outlet } = await import("react-router-dom");
  return {
    AuthProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    ProtectedRoute: () => <Outlet />,
    AdminRoute: () => <Outlet />,
    useAuth: () => ({ user: { id: "1", username: "lera", is_admin: true }, isLoading: false }),
  };
});

function open(path: string) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("маршруты автоматизаций", () => {
  it.each([
    ["/automations/wb-reviews", "отзывы"],
    ["/automations/wb-turnover", "оборачиваемость"],
    ["/automations/wb-fbs-distribution", "распределение FBS"],
  ])("%s открывает свою страницу", (path, expected) => {
    open(path);

    expect(screen.getByText(expected)).toBeInTheDocument();
    // Без своего маршрута адрес провалился бы в общий хвост и вернул каталог —
    // ровно так «Открыть» и уводило бы обратно на список автоматизаций.
    expect(screen.queryByText("каталог автоматизаций")).not.toBeInTheDocument();
  });

  it("неизвестный адрес возвращает в каталог", () => {
    open("/automations/wb-nothing");

    expect(screen.getByText("каталог автоматизаций")).toBeInTheDocument();
  });
});
