import { LogOut, Shield, UserRound } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import { adminEntryUrl } from "../features/admin/entry";
import { logout } from "../features/auth/api";
import { useAuth } from "../features/auth/AuthContext";

interface AppHeaderProps {
  current?: string;
}

export function AppHeader({ current }: AppHeaderProps) {
  const { user, clearSession } = useAuth();
  const navigate = useNavigate();

  async function handleLogout() {
    try {
      await logout();
    } finally {
      clearSession();
      navigate("/login");
    }
  }

  return (
    <header className="app-header">
      <div className="header-left">
        <Link className="wordmark" to="/automations">MARKETPLACE AUTO</Link>
        <nav className="breadcrumb" aria-label="Хлебные крошки">
          <Link to="/automations">Автоматизации</Link>
          {current && <><span>/</span><span className="breadcrumb-current">{current}</span></>}
        </nav>
        <nav className="header-nav" aria-label="Разделы">
          <Link to="/sellers">Селлеры</Link>
        </nav>
      </div>
      <div className="header-profile">
        {user?.is_admin && (
          <a className="section-switch" href={adminEntryUrl("/admin/users")}>
            <Shield size={15} />
            Админка
          </a>
        )}
        <div>
          <span className="eyebrow">Сотрудник</span>
          <strong>{user?.username}</strong>
        </div>
        <span className="profile-icon" aria-hidden="true"><UserRound size={18} /></span>
        <button className="icon-button" onClick={handleLogout} aria-label="Выйти">
          <LogOut size={18} />
        </button>
      </div>
    </header>
  );
}
