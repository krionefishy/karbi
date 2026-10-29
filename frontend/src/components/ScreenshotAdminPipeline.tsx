import { useQuery } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import {
  formatAge,
  formatShare,
  formatStorage,
  modeBadge,
  OLDEST_PROCESSING_WARN_S,
  pipelineAlerts,
  STAGING_WARN_PERCENT,
  VLM_ERROR_WARN,
} from "../features/screenshotControl/admin";
import { getPipeline } from "../features/screenshotControl/api";
import { apiErrorText } from "../features/screenshotControl/errors";
import { toneBadgeClass } from "../features/screenshotControl/format";

const REFRESH_MS = 15_000;
const number = new Intl.NumberFormat("ru-RU");

function Tile({ label, children, tone, small }: { label: string; children: ReactNode; tone?: "positive" | "warning"; small?: boolean }) {
  return (
    <div className={`sca-tile${tone ? ` sca-tile-${tone}` : ""}${small ? " sca-tile-small" : ""}`}>
      <span className="sca-tile-label">{label}</span>
      <span className="sca-tile-value">{children}</span>
    </div>
  );
}

export function ScreenshotAdminPipeline() {
  const pipeline = useQuery({ queryKey: ["sca-pipeline"], queryFn: getPipeline, refetchInterval: REFRESH_MS });

  if (pipeline.isLoading) return <div className="loading-block">Загружаем состояние конвейера…</div>;
  if (!pipeline.data) {
    return (
      <div className="inline-error" role="alert">
        <div>
          <strong>Состояние конвейера не загрузилось</strong>
          {apiErrorText(pipeline.error)}
        </div>
        <button type="button" className="secondary-button sca-retry" onClick={() => void pipeline.refetch()}>
          Повторить
        </button>
      </div>
    );
  }

  const data = pipeline.data;
  const mode = modeBadge(data.mode);
  const inWork = data.stages.processing + data.stages.extracted;
  const stored = data.storage.objects.stored ?? 0;
  const staging = data.storage.staging_used_percent;
  const errors = data.vlm_error_rate_1h;

  return (
    <div className="sca-pipeline">
      {pipeline.isError && (
        <p className="sca-muted-small" role="status">
          Данные не обновились: {apiErrorText(pipeline.error)}
        </p>
      )}
      {pipelineAlerts(data).map((alert) => (
        <div key={alert.key} className="fbs-notice" role="status">
          <span className="fbs-notice-mark">
            <TriangleAlert size={18} aria-hidden="true" />
          </span>
          <div>
            <strong>{alert.title}</strong>
            <p>{alert.text}</p>
          </div>
        </div>
      ))}

      <div className="sca-tiles">
        <Tile label="В обработке">{number.format(inWork)}</Tile>
        <Tile
          label="Старейший в очереди"
          tone={(data.oldest_processing_age_s ?? 0) > OLDEST_PROCESSING_WARN_S ? "warning" : undefined}
        >
          {data.oldest_processing_age_s === null ? "пусто" : formatAge(data.oldest_processing_age_s)}
        </Tile>
        <Tile label="p95 приём → вердикт">{formatAge(data.p95_s)}</Tile>
        <Tile label="Ошибки VLM за час" tone={errors === null ? undefined : errors > VLM_ERROR_WARN ? "warning" : "positive"}>
          {errors === null ? "—" : formatShare(errors)}
        </Tile>
      </div>
      <div className="sca-tiles">
        <Tile label="Режим" small>
          <span className={toneBadgeClass(mode.tone)}>
            <span />
            {mode.label}
          </span>
          <span className="sca-muted-small mono">{data.mode}</span>
        </Tile>
        <Tile label="Файлов / объём" small>
          {number.format(stored)} / {formatStorage(data.storage.stored_bytes)}
        </Tile>
        <Tile label="Временное хранилище" small tone={staging !== null && staging >= STAGING_WARN_PERCENT ? "warning" : undefined}>
          {staging === null ? "—" : `${Math.round(staging)}%`}
        </Tile>
        <Tile label="Токены за сутки" small>
          {number.format(data.tokens_today)}
        </Tile>
      </div>
      <p className="sca-foot">
        Сегодня: расширение {data.channels_today.extension}, Telegram {data.channels_today.telegram}, сайт{" "}
        {data.channels_today.web} · не распознано {data.extraction_failed_today} · медиана {formatAge(data.p50_s)} ·
        Telegram {data.telegram_enabled ? "включён" : "выключен"}
      </p>
    </div>
  );
}
