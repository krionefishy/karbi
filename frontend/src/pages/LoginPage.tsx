import { Eye, EyeOff, LockKeyhole } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "../api/http";
import { login } from "../features/auth/api";
import { useAuth } from "../features/auth/AuthContext";

export function LoginPage() {
  const { user, isLoading, setSession } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [showPassword, setShowPassword] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  if (!isLoading && user) return <Navigate to="/automations" replace />;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setError("");
    setPending(true);
    try {
      const result = await login(String(form.get("username")), String(form.get("password")));
      setSession(result.user, result.access_token);
      const destination = (location.state as { from?: string } | null)?.from ?? "/automations";
      navigate(destination, { replace: true });
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "Не удалось войти");
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="login-page">
      <div className="login-brand">
        <span className="wordmark wordmark-large">MARKETPLACE AUTO</span>
        <span className="status-badge"><span /> Внутренняя система</span>
      </div>
      <section className="login-panel">
        <div className="corner corner-tl" /><div className="corner corner-tr" />
        <div className="corner corner-bl" /><div className="corner corner-br" />
        <p className="eyebrow">Единый контур автоматизаций</p>
        <h1>Вход в систему</h1>
        <p className="muted login-intro">Используйте корпоративные данные для доступа к отчётам и автоматизациям.</p>
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
        <div className="security-note"><LockKeyhole size={15} /><span>Соединение защищено. Не передавайте данные для входа третьим лицам.</span></div>
      </section>
      <footer className="login-footer">MARKETPLACE AUTO / INTERNAL AUTOMATION PLATFORM</footer>
    </main>
  );
}
