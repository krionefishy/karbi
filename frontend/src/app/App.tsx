import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { homeRoute } from "../features/admin/entry";
import { AdminRoute, AuthProvider, ProtectedRoute } from "../features/auth/AuthContext";
import { BotsPage } from "../pages/admin/BotsPage";
import { EmployeesPage } from "../pages/admin/EmployeesPage";
import { LoginPage } from "../pages/LoginPage";
import { AutomationsPage } from "../pages/AutomationsPage";
import { ReviewsPage } from "../pages/ReviewsPage";
import { SellersPage } from "../pages/SellersPage";
import { FbsDistributionPage } from "../pages/FbsDistributionPage";
import { TurnoverPage } from "../pages/TurnoverPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1 },
  },
});

/** Таблица маршрутов отдельно от роутера: так её можно смонтировать в тесте и
 * проверить, что у автоматизации действительно есть своя страница, а не общий
 * перенаправляющий хвост. */
export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route path="/automations" element={<AutomationsPage />} />
        <Route path="/sellers" element={<SellersPage />} />
        <Route path="/automations/wb-reviews" element={<ReviewsPage />} />
        <Route path="/automations/wb-turnover" element={<TurnoverPage />} />
        <Route path="/automations/wb-fbs-distribution" element={<FbsDistributionPage />} />
        <Route element={<AdminRoute />}>
          <Route path="/admin/users" element={<EmployeesPage />} />
          <Route path="/admin/bots" element={<BotsPage />} />
        </Route>
      </Route>
      <Route path="*" element={<Navigate to={homeRoute()} replace />} />
    </Routes>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
