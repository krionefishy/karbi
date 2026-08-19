import { X } from "lucide-react";
import { type FormEvent, useState } from "react";

import type { Seller, SellerInput } from "../features/sellers/types";

interface Props {
  automationTitle: string;
  /** Registry sellers that are not connected to this automation yet. */
  available: Seller[];
  pending: boolean;
  error: string;
  onClose: () => void;
  onConnect: (payload: { seller_id: string } | SellerInput) => void;
}

export function ConnectSellerDialog({
  automationTitle,
  available,
  pending,
  error,
  onClose,
  onConnect,
}: Props) {
  const [mode, setMode] = useState<"existing" | "new">(available.length ? "existing" : "new");

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    if (mode === "existing") {
      onConnect({ seller_id: String(form.get("seller_id")) });
      return;
    }
    onConnect({
      name: String(form.get("name")).trim(),
      api_key: String(form.get("api_key")).trim(),
    });
  }

  return (
    <div className="modal-overlay" role="presentation">
      <section className="seller-dialog" role="dialog" aria-modal="true" aria-labelledby="connect-dialog-title">
        <button className="dialog-close" onClick={onClose} aria-label="Закрыть">
          <X size={18} />
        </button>
        <p className="eyebrow">Подключение к автоматизации</p>
        <h2 id="connect-dialog-title">{automationTitle}</h2>
        <p className="muted">
          Селлер живёт в общем реестре. Подключение к автоматизации ничего не создаёт и ничего не
          удаляет — оно только включает сбор данных.
        </p>
        <div className="mode-switch" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "existing"}
            className={mode === "existing" ? "mode-active" : ""}
            disabled={available.length === 0}
            onClick={() => setMode("existing")}
          >
            Из реестра
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "new"}
            className={mode === "new" ? "mode-active" : ""}
            onClick={() => setMode("new")}
          >
            Новый селлер
          </button>
        </div>
        <form className="seller-form" onSubmit={submit}>
          {mode === "existing" ? (
            <label>
              <span className="field-label">Селлер</span>
              <select name="seller_id" defaultValue={available[0]?.id} required>
                {available.map((seller) => (
                  <option key={seller.id} value={seller.id}>
                    {seller.name}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <>
              <label>
                <span className="field-label">Название селлера</span>
                <input name="name" minLength={2} maxLength={255} required placeholder="ООО Ромашка" />
              </label>
              <label>
                <span className="field-label">WB Seller API Key</span>
                <input name="api_key" type="password" minLength={10} required autoComplete="off" />
              </label>
            </>
          )}
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
