import { createContext, useContext, useEffect, useMemo, useState, type PropsWithChildren } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { setAccessToken } from "../../api/http";
import { restoreSession } from "./api";
import type { CurrentUser } from "./types";

interface AuthValue {
  user: CurrentUser | null;
  isLoading: boolean;
  setSession: (user: CurrentUser, accessToken: string) => void;
  clearSession: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);
export function AuthProvider({ children }: PropsWithChildren) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let active = true;
    restoreSession()
      .then((session) => {
        if (active) setUser(session.user);
      })
      .catch(() => setAccessToken(null))
      .finally(() => {
        if (active) setIsLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const value = useMemo<AuthValue>(
    () => ({
      user,
      isLoading,
      setSession: (nextUser, accessToken) => {
        setAccessToken(accessToken);
        setUser(nextUser);
      },
      clearSession: () => {
        setAccessToken(null);
        setUser(null);
      },
    }),
    [isLoading, user],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}

export function ProtectedRoute() {
  const { user, isLoading } = useAuth();
  const location = useLocation();
  if (isLoading) {
    return <main className="session-loading" aria-label="Восстановление сессии">KARBI</main>;
  }
  if (!user) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return <Outlet />;
}
