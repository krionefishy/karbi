import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { formatAge, formatShare, formatStorage } from "../features/screenshotControl/admin";
import { getPipeline } from "../features/screenshotControl/api";
import { apiErrorText } from "../features/screenshotControl/errors";

const REFRESH_MS = 30_000;
const number = new Intl.NumberFormat("ru-RU");

function Metric({ label, children, tone }: { label: string; children: ReactNode; tone?: "warning" }) {
  return (
    <div className={`sca-tile sca-tile-small${tone ? ` sca-tile-${tone}` : ""}`}>
      <span className="sca-tile-label">{label}</span>
      <span className="sca-tile-value">{children}</span>
    </div>
  );
}

export function ScreenshotAdminMetrics() {
  const metrics = useQuery({ queryKey: ["sca-pipeline"], queryFn: getPipeline, refetchInterval: REFRESH_MS });

  if (metrics.isLoading) return <div className="loading-block">Загружаем метрики…</div>;
  if (!metrics.data) {
    return (
      <div className="inline-error" role="alert">
        <div>
          <strong>Метрики не загрузились</strong>
          {apiErrorText(metrics.error)}
        </div>
        <button type="button" className="secondary-button sca-retry" onClick={() => void metrics.refetch()}>
          Повторить
        </button>
      </div>
    );
  }

  const data = metrics.data;
  const channelTotal = Object.values(data.channels_today).reduce((sum, value) => sum + value, 0);
  const verdictTotal = Object.values(data.auto_verdicts_today).reduce((sum, value) => sum + value, 0);
  const retries = Object.values(data.retry_submissions_today).reduce((sum, value) => sum + value, 0);
  const oldestReview = Math.max(
    data.review_queue.oldest_review_age_s ?? 0,
    data.review_queue.oldest_duplicate_age_s ?? 0,
    data.review_queue.oldest_audit_age_s ?? 0,
  );
  const waitingBytes = (data.storage.bytes_by_state.staged ?? 0) + (data.storage.bytes_by_state.uploading ?? 0);

  return (
    <div className="sca-pipeline">
      {metrics.isError && (
        <p className="sca-muted-small" role="status">
          Данные не обновились: {apiErrorText(metrics.error)}
        </p>
      )}

      <div className="sca-tiles">
        <Metric label="Загрузки сегодня">{number.format(channelTotal)}</Metric>
        <Metric label="Автовердикты сегодня">{number.format(verdictTotal)}</Metric>
        <Metric label="Сабмишены с ретраями">{number.format(retries)}</Metric>
        <Metric label={`Ошибки VLM за час · N=${data.extractions_1h}`}>
          {data.vlm_error_rate_1h === null ? "—" : formatShare(data.vlm_error_rate_1h)}
        </Metric>
      </div>

      <div className="sca-tiles">
        <Metric label="Авто: принято">{number.format(data.auto_verdicts_today.accepted)}</Metric>
        <Metric label="Авто: дубли">{number.format(data.auto_verdicts_today.duplicate)}</Metric>
        <Metric label="Авто: на проверку">{number.format(data.auto_verdicts_today.needs_review)}</Metric>
        <Metric label="Авто: отказ">{number.format(data.auto_verdicts_today.rejected)}</Metric>
      </div>

      <div className="sca-tiles">
        <Metric label="Ручная очередь">
          {number.format(data.review_queue.needs_review)} проверка · {number.format(data.review_queue.duplicates)} дубли ·{" "}
          {number.format(data.review_queue.audit)} аудит
        </Metric>
        <Metric label="Старейший в ручной очереди">{oldestReview ? formatAge(oldestReview) : "пусто"}</Metric>
        <Metric label="Токены успешных распознаваний">{number.format(data.tokens_today)}</Metric>
        <Metric label="Ждёт выгрузки в S3">{formatStorage(waitingBytes)}</Metric>
      </div>

      <div className="sca-tiles">
        <Metric label="DBS outbox" tone={data.outbox.retrying > 0 ? "warning" : undefined}>
          {number.format(data.outbox.pending)} ждут · {number.format(data.outbox.retrying)} с ретраями
        </Metric>
        <Metric label="Расширение">{number.format(data.channels_today.extension)}</Metric>
        <Metric label="Telegram">{number.format(data.channels_today.telegram)}</Metric>
        <Metric label="Сайт">{number.format(data.channels_today.web)}</Metric>
      </div>

      <p className="sca-foot">
        Retry buckets: 1 попытка — {data.retry_submissions_today.one}, 2 — {data.retry_submissions_today.two}, 3 —{" "}
        {data.retry_submissions_today.three}, 4+ — {data.retry_submissions_today.four_plus}. Токены пока учитывают
        только итоговые успешные распознавания уникальных файлов.
      </p>
    </div>
  );
}
