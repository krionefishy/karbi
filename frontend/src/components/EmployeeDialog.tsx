import { Check, Copy, X } from "lucide-react";
import { type FormEvent, useState } from "react";

import type { IssuedPassword } from "../features/admin/types";

interface CreateProps {
  pending: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (value: { username: string; is_admin: boolean }) => void;
}

export function EmployeeDialog({ pending, error, onClose, onSubmit }: CreateProps) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    onSubmit({ username: String(form.get("username")).trim(), is_admin: form.get("is_admin") === "on" });
  }
  return (
    <div className="modal-overlay" role="presentation">
      <section className="seller-dialog" role="dialog" aria-modal="true" aria-labelledby="employee-dialog-title">
        <button className="dialog-close" onClick={onClose} aria-label="Закрыть">
          <X size={18} />
        </button>
        <p className="eyebrow">Доступ / сотрудники</p>
        <h2 id="employee-dialog-title">Добавить сотрудника</h2>
        <p className="muted">Пароль сгенерирует система и покажет один раз — придумывать его не нужно.</p>
        <form className="seller-form" onSubmit={submit}>
          <label>
            <span className="field-label">Логин</span>
            <input name="username" minLength={2} maxLength={255} required autoComplete="off" placeholder="ivanov" />
          </label>
          <label className="checkbox-field">
            <input type="checkbox" name="is_admin" />
            <span>Доступ к админке</span>
          </label>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <div className="dialog-actions">
            <button type="button" className="secondary-button" onClick={onClose}>
              Отмена
            </button>
            <button className="primary-button" disabled={pending}>
              {pending ? "Заводим…" : "Добавить"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

interface PasswordProps {
  issued: IssuedPassword;
  onClose: () => void;
}

export function IssuedPasswordDialog({ issued, onClose }: PasswordProps) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    await navigator.clipboard.writeText(issued.password);
    setCopied(true);
  }

  return (
    <div className="modal-overlay" role="presentation">
      <section className="seller-dialog" role="dialog" aria-modal="true" aria-labelledby="issued-password-title">
        <p className="eyebrow">Доступ / сотрудники</p>
        <h2 id="issued-password-title">Пароль для {issued.user.username}</h2>
        <p className="muted">
          Показывается один раз. Закроете окно — узнать пароль будет нельзя, только выдать новый.
        </p>
        <p className="issued-password">{issued.password}</p>
        <div className="dialog-actions">
          <button type="button" className="secondary-button" onClick={copy}>
            {copied ? <Check size={15} /> : <Copy size={15} />}
            {copied ? "Скопировано" : "Скопировать"}
          </button>
          <button className="primary-button" onClick={onClose}>
            Я передал пароль
          </button>
        </div>
      </section>
    </div>
  );
}
