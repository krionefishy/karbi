import { ArrowLeft, LogOut, UserRound } from "lucide-react";
import { Link, NavLink, useNavigate } from "react-router-dom";

import { workspaceUrl } from "../features/admin/entry";
import { logout } from "../features/auth/api";
import { useAuth } from "../features/auth/AuthContext";

/** The admin section keeps its own header: none of the workspace navigation
 * belongs here, and mixing the two made it unclear which interface you are in. */
export function AdminHeader() {
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
        <Link className="wordmark" to="/admin/users">MARKETPLACE AUTO</Link>
        <span className="admin-badge">Админка</span>
        <nav className="header-nav" aria-label="Разделы админки">
          <NavLink to="/admin/users">Сотрудники</NavLink>
          <NavLink to="/admin/bots">Боты</NavLink>
        </nav>
      </div>
      <div className="header-profile">
        <a className="section-switch" href={workspaceUrl("/automations")}>
          <ArrowLeft size={15} />
          В рабочий интерфейс
        </a>
        <div>
          <span className="eyebrow">Администратор</span>
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
