import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider, ProtectedRoute } from "../features/auth/AuthContext";
import { LoginPage } from "../pages/LoginPage";
import { AutomationsPage } from "../pages/AutomationsPage";
import { ReviewsPage } from "../pages/ReviewsPage";
import { SellersPage } from "../pages/SellersPage";

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
            </Route>
            <Route path="*" element={<Navigate to="/automations" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
