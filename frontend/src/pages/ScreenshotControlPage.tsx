import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ScreenshotConnections } from "../components/ScreenshotConnections";
import { ScreenshotKpi } from "../components/ScreenshotKpi";
import { POLL_MS, ScreenshotList, SUBMISSIONS_KEY } from "../components/ScreenshotList";
import { ScreenshotUpload } from "../components/ScreenshotUpload";
import { Shell } from "../components/Shell";
import { getConfig, getSummary } from "../features/screenshotControl/api";
import { customRangeError, periodRange, type PeriodKind, type PeriodRange } from "../features/screenshotControl/format";

const TITLE = "Контроль скриншотов ДБС";
const SUMMARY_KEY = "sc-summary";

type Tab = "mine" | "connections";

const PERIODS: [PeriodKind, string][] = [
  ["day", "День"],
  ["week", "Неделя"],
  ["month", "Месяц"],
  ["custom", "Свой"],
];

export function ScreenshotControlPage() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("mine");
  const [periodKind, setPeriodKind] = useState<PeriodKind>("day");
  const [custom, setCustom] = useState<PeriodRange>(() => periodRange("week"));

  const config = useQuery({ queryKey: ["sc-config"], queryFn: getConfig, staleTime: 5 * 60_000 });
  const rangeError = periodKind === "custom" ? customRangeError(custom) : "";
  const period = periodKind === "custom" ? custom : periodRange(periodKind);
  const summary = useQuery({
    queryKey: [SUMMARY_KEY, period.from, period.to],
    queryFn: () => getSummary(period),
    // В выключенном режиме сводку не показываем — и не запрашиваем.
    enabled: !rangeError && !config.isPending && config.data?.mode !== "off",
    // Опрос только пока конвейер распознаёт: «нужен исход» ждёт менеджера, а не сервер.
    refetchInterval: (query) => ((query.state.data?.processing ?? 0) > 0 ? POLL_MS : false),
  });

  const onUploaded = () => {
    void queryClient.invalidateQueries({ queryKey: [SUBMISSIONS_KEY] });
    void queryClient.invalidateQueries({ queryKey: [SUMMARY_KEY] });
  };

  return (
    <Shell current={TITLE}>
      <main className="reviews-content sc-page">
        <div className="reviews-heading">
          <div>
            <h1>{TITLE}</h1>
            <p className="muted">
              {tab === "mine" ? "Ваши отчёты по отзывам за период" : "Расширение и бот для отправки скриншотов"}
            </p>
          </div>
          {tab === "mine" && (
            <div className="sc-period">
              <div className="seg" role="group" aria-label="Период">
                {PERIODS.map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    className={periodKind === key ? "seg-active" : undefined}
                    aria-pressed={periodKind === key}
                    onClick={() => setPeriodKind(key)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {periodKind === "custom" && (
                <div className="sc-range">
                  <input
                    type="date"
                    aria-label="Начало периода"
                    value={custom.from}
                    max={custom.to || undefined}
                    onChange={(event) => setCustom({ ...custom, from: event.target.value })}
                  />
                  <span className="muted">—</span>
                  <input
                    type="date"
                    aria-label="Конец периода"
                    value={custom.to}
                    min={custom.from || undefined}
                    onChange={(event) => setCustom({ ...custom, to: event.target.value })}
                  />
                </div>
              )}
              {rangeError && (
                <p className="form-error" role="alert">
                  {rangeError}
                </p>
              )}
            </div>
          )}
        </div>

        <div className="mode-switch" role="tablist" aria-label="Разделы">
          {(
            [
              ["mine", "Мои скриншоты"],
              ["connections", "Подключения"],
            ] as [Tab, string][]
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              id={`sc-tab-${key}`}
              aria-controls="sc-tabpanel"
              aria-selected={tab === key}
              className={tab === key ? "mode-active" : undefined}
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
        </div>

        <div role="tabpanel" id="sc-tabpanel" aria-labelledby={`sc-tab-${tab}`}>
        {config.data?.mode === "off" ? (
          <div className="empty-state">
            <h2>Контроль скриншотов выключен</h2>
            <p>Загрузка временно недоступна. Отправленное раньше сохранено.</p>
          </div>
        ) : tab === "connections" ? (
          <ScreenshotConnections config={config.data} />
        ) : (
          <>
            {!rangeError && (
              <ScreenshotKpi
                summary={summary.data}
                loading={summary.isPending}
                error={summary.error}
                onRetry={() => void summary.refetch()}
              />
            )}

            <div className="sc-columns">
              <ScreenshotUpload limits={config.data} onUploaded={onUploaded} />
              <ScreenshotList />
            </div>
          </>
        )}
        </div>
      </main>
    </Shell>
  );
}
