import { useQuery } from "@tanstack/react-query";

import { ApiError } from "../api/http";
import { getMonitor } from "../features/supportBot/api";
import {
  dayBars,
  groupChecks,
  groupLabel,
  incidentDuration,
  severityLabel,
  severityTone,
  workerStale,
} from "../features/supportBot/format";
import type { MonitorCheck, MonitorIncident } from "../features/supportBot/types";

const REFRESH_MS = 60_000;

const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });
const stamp = (value: string | null) => (value ? momentFormatter.format(new Date(value)) : "—");
const dayFormatter = new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", timeZone: "UTC" });
const dayLabel = (date: string) => dayFormatter.format(new Date(`${date}T00:00:00Z`));

const DAY_MINUTES = 1440;

function message(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

function CheckCard({ check }: { check: MonitorCheck }) {
  const tone = severityTone(check.severity);
  const showHint = check.severity === "warn" || check.severity === "crit";
  return (
    <article className={`mon-card mon-card-${tone}`}>
      <div className="mon-card-head">
        <h4>{check.title}</h4>
        <span className={`rp-chip rp-chip-${tone === "stop" || tone === "warn" || tone === "go" ? tone : "neutral"}`}>
          {severityLabel(check.severity)}
        </span>
      </div>
      <strong className="mon-value">{check.value ?? "—"}</strong>
      <small className="muted">Порог: {check.threshold}</small>
      {showHint && check.hint && <p className="mon-hint">Что делать: {check.hint}</p>}
    </article>
  );
}

function IncidentRow({ incident }: { incident: MonitorIncident }) {
  const tone = incident.severity === "crit" ? "stop" : "warn";
  return (
    <li className="mon-incident">
      <span className="mon-incident-title">{incident.title}</span>
      <span className={`rp-chip rp-chip-${tone}`}>{severityLabel(incident.severity)}</span>
      <span>{stamp(incident.started_at)}</span>
      <span>{incidentDuration(incident)}</span>
      <span className="muted">{incident.value ?? ""}</span>
    </li>
  );
}

export function SupportBotMonitor({ sellerId }: { sellerId: string }) {
  const monitor = useQuery({
    queryKey: ["support-bot-monitor", sellerId],
    queryFn: () => getMonitor(sellerId),
    enabled: Boolean(sellerId),
    refetchInterval: REFRESH_MS,
  });
  const data = monitor.data;
  const tone = data ? severityTone(data.overall) : "neutral";
  const bars = data ? dayBars(data.days) : [];
  const scale = Math.max(60, ...bars.map((bar) => bar.warn + bar.crit));

  return (
    <div className="mon">
      {monitor.error && (
        <div className="inline-error">{message(monitor.error, "Не удалось загрузить состояние")}</div>
      )}
      {!data ? (
        !monitor.error && <div className="loading-block">Загружаем состояние…</div>
      ) : (
        <>
          <div className={`mon-overall mon-overall-${tone}`} role="status">
            <strong>{severityLabel(data.overall)}</strong>
            <span className="muted">Проверено {stamp(data.checked_at)}</span>
          </div>
          {workerStale(data.worker_checked_at) && (
            <div className="inline-error">Воркер бота не отвечает с {stamp(data.worker_checked_at)}</div>
          )}

          {groupChecks(data.checks).map((item) => (
            <section key={item.group}>
              <h3 className="rp-subtitle">{groupLabel(item.group)}</h3>
              <div className="mon-cards">
                {item.checks.map((check) => (
                  <CheckCard key={check.id} check={check} />
                ))}
              </div>
            </section>
          ))}

          <h3 className="rp-subtitle">История за 7 дней</h3>
          <div className="rp-key">
            <span>
              <i style={{ background: "var(--warning-mark)" }} />
              Внимание
            </span>
            <span>
              <i style={{ background: "var(--negative)" }} />
              Проблема
            </span>
          </div>
          <div className="mon-days" role="img" aria-label="Минуты с замечаниями и проблемами по дням">
            {bars.map((bar) => (
              <div
                key={bar.date}
                className="mon-day"
                title={`${dayLabel(bar.date)}: внимание ${bar.warn} мин, проблема ${bar.crit} мин`}
              >
                <div className="mon-day-bar">
                  <i
                    className="mon-day-warn"
                    style={{ height: `${(Math.min(bar.warn, DAY_MINUTES) / scale) * 100}%` }}
                  />
                  <i
                    className="mon-day-crit"
                    style={{ height: `${(Math.min(bar.crit, DAY_MINUTES) / scale) * 100}%` }}
                  />
                </div>
                <small>{dayLabel(bar.date)}</small>
              </div>
            ))}
          </div>

          {data.incidents.length === 0 ? (
            <div className="empty-state">
              <p>Проблем за 7 дней не было</p>
            </div>
          ) : (
            <ul className="mon-incidents">
              {data.incidents.map((incident) => (
                <IncidentRow key={`${incident.check_id}-${incident.started_at}`} incident={incident} />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
