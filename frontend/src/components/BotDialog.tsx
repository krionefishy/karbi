import { X } from "lucide-react";
import { type FormEvent, useState } from "react";

import type { BotCreate } from "../features/admin/types";

interface Props {
  pending: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (value: BotCreate) => void;
}

/** Registering a bot is the one place a messenger token is typed into this
 * interface. It goes to the relay outside Russia and is not stored here, so
 * there is nothing to show back afterwards and nothing to edit later. */
export function BotDialog({ pending, error, onClose, onSubmit }: Props) {
  const [visible, setVisible] = useState(false);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    onSubmit({
      code: String(form.get("code")).trim(),
      title: String(form.get("title")).trim(),
      token: String(form.get("token")).trim(),
    });
  }

  return (
    <div className="modal-overlay" role="presentation">
      <section className="seller-dialog" role="dialog" aria-modal="true" aria-labelledby="bot-dialog-title">
        <button className="dialog-close" onClick={onClose} aria-label="Закрыть">
          <X size={18} />
        </button>
        <p className="eyebrow">Уведомления / боты</p>
        <h2 id="bot-dialog-title">Подключить бота</h2>
        <p className="muted">
          Токен получите у @BotFather. Он уедет в сервис доставки и здесь не сохранится — показать его
          повторно будет неоткуда.
        </p>
        <form className="seller-form" onSubmit={submit}>
          <label>
            <span className="field-label">Код</span>
            <input
              name="code"
              required
              maxLength={64}
              pattern="[a-z0-9][a-z0-9._\-]*"
              autoComplete="off"
              placeholder="turnover-alerts"
            />
            <span className="muted field-note">
              Им автоматизации адресуют бота. Латиница в нижнем регистре, цифры, дефис.
            </span>
          </label>
          <label>
            <span className="field-label">Название</span>
            <input name="title" maxLength={255} autoComplete="off" placeholder="Оборачиваемость" />
            <span className="muted field-note">Необязательно: пустое — возьмём имя бота из мессенджера.</span>
          </label>
          <label>
            <span className="field-label">Токен</span>
            <span className="password-field">
              <input
                name="token"
                type={visible ? "text" : "password"}
                required
                autoComplete="off"
                placeholder="123456789:AA..."
              />
              <button type="button" onClick={() => setVisible((value) => !value)}>
                {visible ? "Скрыть" : "Показать"}
              </button>
            </span>
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
              {pending ? "Подключаем…" : "Подключить"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
