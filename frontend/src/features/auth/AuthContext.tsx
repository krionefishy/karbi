import { createContext, useContext, useEffect, useMemo, useState, type PropsWithChildren } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { ApiError, setAccessDeniedHandler, setAccessToken } from "../../api/http";
import { getCurrentUser, restoreSession } from "./api";
import type { CurrentUser } from "./types";

interface AuthValue {
  user: CurrentUser | null;
  isLoading: boolean;
  setSession: (user: CurrentUser, accessToken: string) => void;
  clearSession: () => void;
  refreshUser: () => Promise<void>;
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
      // Токен сбрасываем, только если сессии действительно нет: 429 от лимитера
      // при восстановлении не повод показывать форму входа.
      .catch((error: unknown) => {
        if (!(error instanceof ApiError) || error.status === 401 || error.status === 403) {
          setAccessToken(null);
        }
      })
      .finally(() => {
        if (active) setIsLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const signedIn = user !== null;
  useEffect(() => {
    if (!signedIn) return;
    // Доступы меняет администратор, пока человек в сессии: меню догоняет их при возврате на вкладку
    // и сразу после 403 «нет доступа к сервису».
    const refresh = () => {
      getCurrentUser()
        .then(setUser)
        .catch(() => undefined);
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    setAccessDeniedHandler(refresh);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      setAccessDeniedHandler(null);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [signedIn]);

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
      refreshUser: async () => {
        setUser(await getCurrentUser());
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
    return <main className="session-loading" aria-label="Восстановление сессии">MARKETPLACE AUTO</main>;
  }
  if (!user) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return <Outlet />;
}

export function AdminRoute() {
  const { user } = useAuth();
  if (!user?.is_admin) {
    return <Navigate to="/automations" replace />;
  }
  return <Outlet />;
}
