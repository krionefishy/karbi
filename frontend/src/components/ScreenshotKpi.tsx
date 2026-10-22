import { AlertTriangle } from "lucide-react";

import { ApiError } from "../api/http";
import type { Summary } from "../features/screenshotControl/types";

interface ScreenshotKpiProps {
  summary: Summary | undefined;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
}

export function ScreenshotKpi({ summary, loading, error, onRetry }: ScreenshotKpiProps) {
  if (loading) return <div className="loading-block sc-kpi-loading">Загружаем сводку…</div>;
  if (error || !summary) {
    return (
      <div className="inline-error" role="alert">
        <div>
          <strong>Не удалось загрузить сводку</strong>
          {error instanceof ApiError ? error.message : "Сервер не ответил."}{" "}
          <button className="secondary-button docs-retry" onClick={onRetry}>
            Повторить
          </button>
        </div>
      </div>
    );
  }

  const blocks = [
    { key: "positive", title: "Положительные", value: summary.counts.positive, caption: "полож" },
    { key: "amended", title: "Доп", value: summary.counts.amended, caption: "отзыв изменён" },
    { key: "deleted", title: "Удалённые", value: summary.counts.deleted, caption: "удален" },
    { key: "review", title: "Требуют ревью", value: summary.pending_review, caption: "ждут админа" },
  ];

  return (
    <>
      {summary.preliminary && (
        <div className="docs-banner docs-banner-warning sc-preliminary" role="status">
          <span className="docs-banner-mark" aria-hidden="true">
            <AlertTriangle size={18} />
          </span>
          <div>
            <strong>Пробный период</strong>
            <p>Цифры предварительные и в KPI пока не идут.</p>
          </div>
        </div>
      )}
      <section className="sc-kpi" aria-label="Сводка за период">
        <div className="sc-kpi-blocks">
          {blocks.map((block) => (
            <div key={block.key} className={`sc-kpi-block sc-kpi-${block.key}`}>
              <span className="sc-kpi-title">{block.title}</span>
              <strong className="sc-kpi-value" aria-label={`${block.title}: ${block.value}`}>
                {block.value}
              </strong>
              <span className="sc-kpi-caption">{block.caption}</span>
            </div>
          ))}
        </div>
        <p className="sc-kpi-foot">
          Не засчитано: дубли {summary.duplicates}, отклонено {summary.rejected}
        </p>
      </section>
    </>
  );
}
