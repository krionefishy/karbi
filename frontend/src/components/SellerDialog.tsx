import { Eye, EyeOff, X } from "lucide-react";
import { type FormEvent, useState } from "react";

import type { OzonCredentialsInput, Seller, SellerInput } from "../features/sellers/types";
import { isOzonMissing } from "../features/sellers/types";

interface Props {
  seller?: Seller;
  pending: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (value: SellerInput | Partial<SellerInput>) => void;
}

export function SellerDialog({ seller, pending, error, onClose, onSubmit }: Props) {
  const [showKey, setShowKey] = useState(false);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name")).trim();
    const apiKey = String(form.get("api_key")).trim();
    onSubmit(seller ? { name, ...(apiKey ? { api_key: apiKey } : {}) } : { name, api_key: apiKey });
  }
  return (
    <div className="modal-overlay" role="presentation">
      <section
        className="seller-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="seller-dialog-title"
      >
        <button className="dialog-close" onClick={onClose} aria-label="Закрыть">
          <X size={18} />
        </button>
        <p className="eyebrow">Wildberries / подключение</p>
        <h2 id="seller-dialog-title">{seller ? "Редактировать селлера" : "Добавить селлера"}</h2>
        <p className="muted">
          Ключ хранится в зашифрованном виде и никогда не отображается после сохранения.
        </p>
        <form className="seller-form" onSubmit={submit}>
          <label>
            <span className="field-label">Название селлера</span>
            <input
              name="name"
              defaultValue={seller?.name}
              minLength={2}
              maxLength={255}
              required
              placeholder="ООО Ромашка"
            />
          </label>
          <label>
            <span className="field-label">WB Seller API Key</span>
            <span className="password-field">
              <input
                name="api_key"
                type={showKey ? "text" : "password"}
                minLength={seller ? undefined : 10}
                required={!seller}
                autoComplete="off"
                placeholder={seller ? "Оставьте пустым, чтобы не менять" : "Вставьте API-ключ"}
              />
              <button
                type="button"
                onClick={() => setShowKey((value) => !value)}
                aria-label="Показать ключ"
              >
                {showKey ? <EyeOff size={17} /> : <Eye size={17} />}
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
              {pending ? "Сохраняем…" : seller ? "Сохранить" : "Добавить селлера"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}

interface ConfirmProps {
  title: string;
  description: string;
  confirmLabel: string;
  pendingLabel: string;
  pending: boolean;
  danger?: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export function ConfirmDialog({
  title,
  description,
  confirmLabel,
  pendingLabel,
  pending,
  danger = false,
  onClose,
  onConfirm,
}: ConfirmProps) {
  return (
    <div className="modal-overlay">
      <section className={`delete-dialog${danger ? "" : " neutral-dialog"}`} role="alertdialog" aria-modal="true">
        <h2>{title}</h2>
        <p>{description}</p>
        <div className="dialog-actions">
          <button className="secondary-button" onClick={onClose}>
            Отмена
          </button>
          <button
            className={danger ? "danger-button" : "primary-button"}
            disabled={pending}
            onClick={onConfirm}
          >
            {pending ? pendingLabel : confirmLabel}
          </button>
        </div>
      </section>
    </div>
  );
}

interface RestoreProps {
  seller: Seller;
  pending: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (apiKey: string) => void;
}

export function RestoreSellerDialog({ seller, pending, error, onClose, onSubmit }: RestoreProps) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    onSubmit(String(new FormData(event.currentTarget).get("api_key")).trim());
  }
  return (
    <div className="modal-overlay" role="presentation">
      <section className="seller-dialog" role="dialog" aria-modal="true" aria-labelledby="restore-dialog-title">
        <button className="dialog-close" onClick={onClose} aria-label="Закрыть">
          <X size={18} />
        </button>
        <p className="eyebrow">Wildberries / восстановление</p>
        <h2 id="restore-dialog-title">Вернуть «{seller.name}» в работу</h2>
        <p className="muted">
          При архивации ключ был удалён — введите его заново. История, собранная до архивации, на
          месте, автоматизации нужно подключить заново.
        </p>
        <form className="seller-form" onSubmit={submit}>
          <label>
            <span className="field-label">WB Seller API Key</span>
            <input name="api_key" type="password" minLength={10} required autoComplete="off" />
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
              {pending ? "Восстанавливаем…" : "Восстановить"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}


interface OzonProps {
  seller: Seller;
  pending: boolean;
  verifying: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (value: OzonCredentialsInput) => void;
  onVerify: () => void;
}

export function OzonCredentialsDialog({
  seller,
  pending,
  verifying,
  error,
  onClose,
  onSubmit,
  onVerify,
}: OzonProps) {
  const [showSecrets, setShowSecrets] = useState(false);
  const missing = isOzonMissing(seller);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    onSubmit({
      client_id: String(form.get("client_id")).trim(),
      api_key: String(form.get("api_key")).trim(),
      performance_client_id: String(form.get("performance_client_id")).trim(),
      performance_client_secret: String(form.get("performance_client_secret")).trim(),
    });
  }
  return (
    <div className="modal-overlay" role="presentation">
      <section className="seller-dialog" role="dialog" aria-modal="true" aria-labelledby="ozon-dialog-title">
        <button className="dialog-close" onClick={onClose} aria-label="Закрыть">
          <X size={18} />
        </button>
        <p className="eyebrow">Ozon / подключение</p>
        <h2 id="ozon-dialog-title">
          {missing ? `Подключить Ozon для «${seller.name}»` : `Учётка Ozon «${seller.name}»`}
        </h2>
        <p className="muted">
          Ключи хранятся на шлюзе и никогда не отображаются после сохранения. Учётка заменяется
          целиком: при замене введите все значения заново.
        </p>
        {!missing && (
          <p className="muted">
            Сейчас: {seller.ozon_egress_status}
            {seller.ozon_egress_error ? ` — ${seller.ozon_egress_error}` : ""}
          </p>
        )}
        <form className="seller-form" onSubmit={submit}>
          <label>
            <span className="field-label">Client-Id</span>
            <input name="client_id" required maxLength={64} autoComplete="off" placeholder="111222" />
          </label>
          <label>
            <span className="field-label">Api-Key (Seller API)</span>
            <span className="password-field">
              <input
                name="api_key"
                type={showSecrets ? "text" : "password"}
                minLength={10}
                required
                autoComplete="off"
                placeholder="Вставьте ключ"
              />
              <button type="button" onClick={() => setShowSecrets((value) => !value)} aria-label="Показать ключи">
                {showSecrets ? <EyeOff size={17} /> : <Eye size={17} />}
              </button>
            </span>
          </label>
          {/* Пара Performance необязательна: без неё работает Seller API, а
              рекламные методы отвечают внятным отказом. Но задаётся целиком —
              один client_id без секрета это неработающая пара, и шлюз её
              отклонит. */}
          <label>
            <span className="field-label">Performance client_id — необязательно</span>
            <input
              name="performance_client_id"
              maxLength={128}
              autoComplete="off"
              placeholder="42@advertising.performance.ozon.ru"
            />
          </label>
          <label>
            <span className="field-label">Performance client_secret</span>
            <input
              name="performance_client_secret"
              type={showSecrets ? "text" : "password"}
              autoComplete="off"
              placeholder="Только вместе с client_id"
            />
          </label>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <div className="dialog-actions">
            {!missing && (
              <button
                type="button"
                className="secondary-button"
                disabled={verifying}
                onClick={onVerify}
              >
                {verifying ? "Проверяем…" : "Перепроверить"}
              </button>
            )}
            <button type="button" className="secondary-button" onClick={onClose}>
              Отмена
            </button>
            <button className="primary-button" disabled={pending}>
              {pending ? "Сохраняем…" : missing ? "Подключить" : "Заменить учётку"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
