import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { homeRoute } from "../features/admin/entry";
import { AdminRoute, AuthProvider, ProtectedRoute } from "../features/auth/AuthContext";
import { ServiceRoute } from "../features/auth/ServiceRoute";
import { BotsPage } from "../pages/admin/BotsPage";
import { EmployeesPage } from "../pages/admin/EmployeesPage";
import { LoginPage } from "../pages/LoginPage";
import { AutomationsPage } from "../pages/AutomationsPage";
import { CardChecklistPage } from "../pages/CardChecklistPage";
import { CardDocumentsPage } from "../pages/CardDocumentsPage";
import { ReviewChatsPage } from "../pages/ReviewChatsPage";
import { SupportBotPage } from "../pages/SupportBotPage";
import { ReviewsPage } from "../pages/ReviewsPage";
import { SellersPage } from "../pages/SellersPage";
import { FbsPenaltiesPage } from "../pages/FbsPenaltiesPage";
import { PodsortPage } from "../pages/PodsortPage";
import { BoxStickersPage } from "../pages/BoxStickersPage";
import { FinReportsPage } from "../pages/FinReportsPage";
import { ReturnsPage } from "../pages/ReturnsPage";
import { FbsStocksPage } from "../pages/FbsStocksPage";
import { TurnoverPage } from "../pages/TurnoverPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1 },
  },
});

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<ProtectedRoute />}>
        <Route path="/automations" element={<AutomationsPage />} />
        <Route path="/sellers" element={<SellersPage />} />
        <Route element={<ServiceRoute />}>
          <Route path="/automations/wb-reviews" element={<ReviewsPage />} />
          <Route path="/automations/wb-turnover" element={<TurnoverPage />} />
          <Route path="/automations/wb-card-checklist" element={<CardChecklistPage />} />
          <Route path="/automations/wb-card-documents" element={<CardDocumentsPage />} />
          <Route path="/automations/wb-fbs-stocks" element={<FbsStocksPage />} />
          <Route path="/automations/wb-fbs-penalties" element={<FbsPenaltiesPage />} />
          <Route path="/automations/wb-review-chats" element={<ReviewChatsPage />} />
          <Route path="/automations/wb-support-bot" element={<SupportBotPage />} />
          <Route path="/automations/wb-returns" element={<ReturnsPage />} />
          <Route path="/automations/wb-podsort" element={<PodsortPage />} />
          <Route path="/automations/wb-box-stickers" element={<BoxStickersPage />} />
          <Route path="/automations/fin-reports" element={<FinReportsPage />} />
        </Route>
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
