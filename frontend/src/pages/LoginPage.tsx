import { Eye, EyeOff } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "../api/http";
import { homeRoute } from "../features/admin/entry";
import { login } from "../features/auth/api";
import { useAuth } from "../features/auth/AuthContext";

export function LoginPage() {
  const { user, isLoading, setSession } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [showPassword, setShowPassword] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  if (!isLoading && user) return <Navigate to={homeRoute()} replace />;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setError("");
    setPending(true);
    try {
      const result = await login(String(form.get("username")), String(form.get("password")));
      setSession(result.user, result.access_token);
      const destination = (location.state as { from?: string } | null)?.from ?? homeRoute();
      navigate(destination, { replace: true });
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "Не удалось войти");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-panel">
        <span className="wordmark wordmark-large">Marketplace Auto</span>
        <h1>Вход в систему</h1>
        <p className="login-intro">Используйте корпоративные данные для доступа к отчётам и автоматизациям.</p>
        <form onSubmit={handleSubmit} className="login-form">
          <label>
            <span className="field-label">Логин</span>
            <input name="username" autoComplete="username" required placeholder="employee" />
          </label>
          <label>
            <span className="field-label">Пароль</span>
            <span className="password-field">
              <input name="password" type={showPassword ? "text" : "password"} autoComplete="current-password" required placeholder="Введите пароль" />
              <button type="button" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? "Скрыть пароль" : "Показать пароль"}>
                {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </span>
          </label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button className="primary-button login-button" disabled={pending}>{pending ? "Входим…" : "Войти"}</button>
        </form>
        <p className="security-note">Соединение защищено. Не передавайте данные для входа третьим лицам.</p>
      </section>
    </main>
  );
}
