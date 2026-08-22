import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { homeRoute } from "../features/admin/entry";
import { AdminRoute, AuthProvider, ProtectedRoute } from "../features/auth/AuthContext";
import { EmployeesPage } from "../pages/admin/EmployeesPage";
import { LoginPage } from "../pages/LoginPage";
import { AutomationsPage } from "../pages/AutomationsPage";
import { ReviewsPage } from "../pages/ReviewsPage";
import { SellersPage } from "../pages/SellersPage";
import { TurnoverPage } from "../pages/TurnoverPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1 },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route element={<ProtectedRoute />}>
              <Route path="/automations" element={<AutomationsPage />} />
              <Route path="/sellers" element={<SellersPage />} />
              <Route path="/automations/wb-reviews" element={<ReviewsPage />} />
              <Route path="/automations/wb-turnover" element={<TurnoverPage />} />
              <Route element={<AdminRoute />}>
                <Route path="/admin/users" element={<EmployeesPage />} />
              </Route>
            </Route>
            <Route path="*" element={<Navigate to={homeRoute()} replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
