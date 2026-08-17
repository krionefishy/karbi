import { createContext, useContext, useMemo, useState, type PropsWithChildren } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import type { CurrentUser } from "./types";

interface AuthValue {
  user: CurrentUser | null;
  setSession: (user: CurrentUser, accessToken: string) => void;
  clearSession: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);
const USER_KEY = "karbi.user";

function readUser(): CurrentUser | null {
  try {
    const value = sessionStorage.getItem(USER_KEY);
    return value ? (JSON.parse(value) as CurrentUser) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: PropsWithChildren) {
  const [user, setUser] = useState<CurrentUser | null>(readUser);
  const value = useMemo<AuthValue>(
    () => ({
      user,
      setSession: (nextUser, accessToken) => {
        sessionStorage.setItem("karbi.access_token", accessToken);
        sessionStorage.setItem(USER_KEY, JSON.stringify(nextUser));
        setUser(nextUser);
      },
      clearSession: () => {
        sessionStorage.removeItem("karbi.access_token");
        sessionStorage.removeItem(USER_KEY);
        setUser(null);
      },
    }),
    [user],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}

export function ProtectedRoute() {
  const { user } = useAuth();
  const location = useLocation();
  if (!user) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return <Outlet />;
}
