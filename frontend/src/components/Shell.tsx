import {
  ArrowLeft,
  Bot,
  LogOut,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Shield,
  Store,
  Sun,
  UserRound,
  Users,
} from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";

import { adminEntryUrl, workspaceUrl } from "../features/admin/entry";
import { logout } from "../features/auth/api";
import { useAuth } from "../features/auth/AuthContext";
import { automationNav } from "../features/automations/nav";
import { useTheme } from "../features/theme";

interface ShellProps {
  /** Текущая страница в хлебных крошках; без неё крошки показывают только раздел. */
  current?: string;
  /** Переключатель селлера, если страница работает с одним селлером. */
  seller?: ReactNode;
  /** Действия страницы в верхней панели — не больше двух. */
  actions?: ReactNode;
  /** Админка: своя навигация и ссылка обратно в рабочий интерфейс. */
  admin?: boolean;
  children: ReactNode;
}

const SIDEBAR_KEY = "marketplace-auto.sidebar";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_KEY) === "collapsed";
  } catch {
    return false;
  }
}

/** Каркас рабочей страницы: левая навигация, верхняя панель, контент.
 * Селлер выбирается в верхней панели, а не в отдельной колонке — широким
 * таблицам нужна вся ширина. */
export function Shell({ current, seller, actions, admin = false, children }: ShellProps) {
  const { user, clearSession } = useAuth();
  const navigate = useNavigate();
  const { theme, toggle } = useTheme();
  const [collapsed, setCollapsed] = useState(readCollapsed);

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_KEY, collapsed ? "collapsed" : "open");
    } catch {
      /* без хранилища состояние живёт до перезагрузки */
    }
  }, [collapsed]);

  async function handleLogout() {
    try {
      await logout();
    } finally {
      clearSession();
      navigate("/login");
    }
  }

  const rootLabel = admin ? "Админка" : "Автоматизации";
  const rootPath = admin ? "/admin/users" : "/automations";

  return (
    <div className={`shell ${collapsed ? "shell-collapsed" : ""}`}>
      <aside className="shell-nav" aria-label="Навигация">
        <div className="shell-brand">
          <Link className="wordmark" to={rootPath}>
            {collapsed ? "MA" : "Marketplace Auto"}
          </Link>
          <button
            className="shell-collapse"
            onClick={() => setCollapsed((value) => !value)}
            aria-label={collapsed ? "Развернуть навигацию" : "Свернуть навигацию"}
          >
            {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
          </button>
        </div>
        {admin ? (
          <nav className="shell-links" aria-label="Разделы админки">
            <NavLink to="/admin/users" title="Сотрудники">
              <Users size={20} />
              <span>Сотрудники</span>
            </NavLink>
            <NavLink to="/admin/bots" title="Боты">
              <Bot size={20} />
              <span>Боты</span>
            </NavLink>
          </nav>
        ) : (
          <nav className="shell-links" aria-label="Автоматизации">
            {automationNav.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink key={item.id} to={`/automations/${item.id}`} title={item.title}>
                  <Icon size={20} />
                  <span>{item.title}</span>
                </NavLink>
              );
            })}
            <NavLink to="/sellers" className="shell-link-section" title="Селлеры">
              <Store size={20} />
              <span>Селлеры</span>
            </NavLink>
          </nav>
        )}
        <div className="shell-foot">
          {admin ? (
            <a className="shell-link" href={workspaceUrl("/automations")} title="В рабочий интерфейс">
              <ArrowLeft size={20} />
              <span>В рабочий интерфейс</span>
            </a>
          ) : (
            user?.is_admin && (
              <a className="shell-link" href={adminEntryUrl("/admin/users")} title="Админка">
                <Shield size={20} />
                <span>Админка</span>
              </a>
            )
          )}
          <div className="shell-user">
            <span className="shell-avatar" aria-hidden="true">
              <UserRound size={16} />
            </span>
            <span className="shell-user-text">
              <strong>{user?.username}</strong>
              <small>{admin ? "Администратор" : "Сотрудник"}</small>
            </span>
            <button className="ghost-button icon-button" onClick={handleLogout} aria-label="Выйти">
              <LogOut size={16} />
            </button>
          </div>
        </div>
      </aside>
      <div className="shell-main">
        <header className="shell-top">
          <nav className="breadcrumb" aria-label="Хлебные крошки">
            <Link to={rootPath}>{rootLabel}</Link>
            {current && (
              <>
                <span aria-hidden="true">/</span>
                <span className="breadcrumb-current">{current}</span>
              </>
            )}
          </nav>
          <div className="shell-top-tools">
            {seller}
            {actions}
            <button
              className="ghost-button icon-button"
              onClick={toggle}
              aria-label={theme === "dark" ? "Светлая тема" : "Тёмная тема"}
            >
              {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
            </button>
          </div>
        </header>
        <div className="shell-content">{children}</div>
      </div>
    </div>
  );
}
