import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api/http";
import { managersCsv, managersCsvName, managerSignals, toneBadgeClass } from "../features/screenshotControl/admin";
import { getManagers } from "../features/screenshotControl/api";
import { customRangeError, periodRange, type PeriodKind, type PeriodRange } from "../features/screenshotControl/format";

const PERIODS: [PeriodKind, string][] = [
  ["day", "День"],
  ["week", "Неделя"],
  ["month", "Месяц"],
  ["custom", "Свой"],
];

function download(text: string, filename: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export function ScreenshotAdminManagers() {
  const [kind, setKind] = useState<PeriodKind>("month");
  const [custom, setCustom] = useState<PeriodRange>(() => periodRange("month"));
  const rangeError = kind === "custom" ? customRangeError(custom) : "";
  const period = kind === "custom" ? custom : periodRange(kind);

  const managers = useQuery({
    queryKey: ["sca-managers", period.from, period.to],
    queryFn: () => getManagers(period),
    enabled: !rangeError,
  });
  const items = managers.data?.items ?? [];

  return (
    <div className="sca-managers">
      <div className="sca-filters">
        <div className="seg" role="group" aria-label="Период">
          {PERIODS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={kind === key ? "seg-active" : undefined}
              aria-pressed={kind === key}
              onClick={() => setKind(key)}
            >
              {label}
            </button>
          ))}
        </div>
        {kind === "custom" && (
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
        {rangeError && <p className="form-error">{rangeError}</p>}
        <span className="sca-hint">Сигналы для разговора, а не санкции</span>
        <button
          type="button"
          className="secondary-button sca-small sca-push"
          disabled={items.length === 0}
          onClick={() => download(managersCsv(items, period), managersCsvName(period))}
        >
          <Download size={14} />
          Выгрузить CSV
        </button>
      </div>

      {rangeError ? null : managers.isLoading ? (
        <div className="loading-block">Считаем статистику…</div>
      ) : managers.isError ? (
        <div className="inline-error" role="alert">
          <div>
            <strong>Статистика не загрузилась</strong>
            {managers.error instanceof ApiError ? managers.error.message : "Нет связи с сервером"}
          </div>
          <button type="button" className="secondary-button sca-retry" onClick={() => void managers.refetch()}>
            Повторить
          </button>
        </div>
      ) : items.length === 0 ? (
        <div className="empty-state sca-empty">
          <h2>За период скриншотов нет</h2>
          <p>Выберите период длиннее или загляните позже, когда менеджеры начнут присылать отчёты.</p>
        </div>
      ) : (
        <div className="card sca-table-card">
          <table className="registry-table sca-table">
            <thead>
              <tr>
                <th>Менеджер</th>
                <th>Всего</th>
                <th>Полож</th>
                <th>Доп</th>
                <th>Удален</th>
                <th>Дубли</th>
                <th>На ревью</th>
                <th>Сигнал</th>
              </tr>
            </thead>
            <tbody>
              {items.map((stats) => (
                <tr key={stats.user.id}>
                  <td>{stats.user.username || "удалённый сотрудник"}</td>
                  <td className="mono">{stats.total}</td>
                  <td className="mono">{stats.positive}</td>
                  <td className="mono">{stats.amended}</td>
                  <td className="mono">{stats.deleted}</td>
                  <td className="mono">{stats.duplicate}</td>
                  <td className="mono">{stats.needs_review}</td>
                  <td>
                    <span className="sca-signals">
                      {managerSignals(stats).map((badge) => (
                        <span key={badge.label} className={toneBadgeClass(badge.tone)}>
                          <span />
                          {badge.label}
                        </span>
                      ))}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
