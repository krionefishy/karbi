import { X } from "lucide-react";
import { type FormEvent, useState } from "react";

interface Props {
  sellerName: string;
  pending: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (dateFrom: string, dateTo: string) => void;
}

const DAY_MS = 24 * 60 * 60 * 1000;

function isoDaysAgo(days: number): string {
  return new Date(Date.now() - days * DAY_MS).toISOString().slice(0, 10);
}

export function ReviewReportDialog({ sellerName, pending, error, onClose, onSubmit }: Props) {
  const [dateFrom, setDateFrom] = useState(() => isoDaysAgo(29));
  const [dateTo, setDateTo] = useState(() => isoDaysAgo(0));
  const invalidRange = Boolean(dateFrom && dateTo && dateFrom > dateTo);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (invalidRange) return;
    onSubmit(dateFrom, dateTo);
  }

  return (
    <div className="modal-overlay" role="presentation">
      <section
        className="seller-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="review-report-dialog-title"
      >
        <button className="dialog-close" onClick={onClose} aria-label="Закрыть">
          <X size={18} />
        </button>
        <p className="eyebrow">Wildberries / отзывы</p>
        <h2 id="review-report-dialog-title">Отчёт по отзывам</h2>
        <p className="muted">
          Excel-файл по «{sellerName}»: артикул, название и количество отзывов на каждую дату
          периода.
        </p>
        <form className="seller-form" onSubmit={submit}>
          <label>
            <span className="field-label">Начало периода</span>
            <input
              name="date_from"
              type="date"
              required
              value={dateFrom}
              onChange={(event) => setDateFrom(event.target.value)}
            />
          </label>
          <label>
            <span className="field-label">Конец периода</span>
            <input
              name="date_to"
              type="date"
              required
              value={dateTo}
              onChange={(event) => setDateTo(event.target.value)}
            />
          </label>
          {invalidRange && (
            <p className="form-error" role="alert">
              Начало периода позже его конца
            </p>
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
            <button className="primary-button" disabled={pending || invalidRange}>
              {pending ? "Формируем…" : "Скачать XLSX"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
