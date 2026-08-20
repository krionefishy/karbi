import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Gauge, MessageSquareText } from "lucide-react";
import { Link } from "react-router-dom";

import { AppHeader } from "../components/AppHeader";
import { getAutomations, getPlatformReadiness } from "../features/automations/api";
import type { AutomationStatus } from "../features/automations/types";

const statusLabels: Record<AutomationStatus, string> = {
  active: "Активна",
  running: "Идёт синхронизация",
  degraded: "Прошла с ошибками",
  failed: "Последний запуск упал",
  idle: "Ещё не запускалась",
};

function sellerLabel(count: number | null) {
  const value = count ?? 0;
  if (value % 10 === 1 && value % 100 !== 11) return `${value} селлер`;
  if ([2, 3, 4].includes(value % 10) && ![12, 13, 14].includes(value % 100)) return `${value} селлера`;
  return `${value} селлеров`;
}

const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });

function momentLabel(value: string | null, fallback = "—") {
  return value ? momentFormatter.format(new Date(value)) : fallback;
}

function durationLabel(seconds: number | null) {
  if (seconds === null) return null;
  if (seconds < 60) return `${seconds} с`;
  const minutes = Math.floor(seconds / 60);
  return seconds % 60 ? `${minutes} мин ${seconds % 60} с` : `${minutes} мин`;
}

export function AutomationsPage() {
  const { data = [], isLoading } = useQuery({ queryKey: ["automations"], queryFn: getAutomations });
  const readiness = useQuery({
    queryKey: ["platform-readiness"],
    queryFn: getPlatformReadiness,
    refetchInterval: 30_000,
    retry: 1,
  });
  const readinessState = readiness.isPending ? "checking" : readiness.isSuccess ? "ready" : "degraded";
  const readinessText = {
    checking: "Проверяем платформу",
    ready: "API и хранилища доступны",
    degraded: "Проблема API или хранилищ",
  }[readinessState];

  return (
    <div className="app-page">
      <AppHeader />
      <main className="page-container automations-page">
        <div className="page-heading">
          <div>
            <p className="eyebrow">Рабочее пространство</p>
            <h1>Автоматизации</h1>
            <p className="muted">Выберите процесс, с данными которого хотите работать.</p>
          </div>
          <span className={`system-status system-status-${readinessState}`} role="status">
            <i /> {readinessText}
          </span>
        </div>
        {isLoading ? (
          <div className="loading-block">Загружаем автоматизации…</div>
        ) : (
          <section className="automation-grid" aria-label="Доступные автоматизации">
            {data.map((automation) => {
              return (
                <article className="automation-card automation-active" key={automation.id}>
                  <div className="automation-card-top">
                    <span className="automation-icon">
                      {automation.id === "wb-turnover" ? <Gauge size={22} /> : <MessageSquareText size={22} />}
                    </span>
                    <span className={`status-badge status-${automation.status}`}>
                      <span />
                      {statusLabels[automation.status]}
                    </span>
                  </div>
                  <h2>{automation.title}</h2>
                  <p className="muted">{automation.description}</p>
                  <dl className="automation-meta">
                    <div>
                      <dt>Селлеров</dt>
                      <dd>{sellerLabel(automation.seller_count)}</dd>
                    </div>
                    <div>
                      <dt>Последний запуск</dt>
                      <dd>
                        {momentLabel(automation.last_run?.finished_at ?? automation.last_run?.created_at ?? null,
                          "ещё не запускалась")}
                        {automation.last_run && (
                          <span className="automation-run-detail">
                            {automation.last_run.completed_sellers} из {automation.last_run.total_sellers}
                            {automation.last_run.failed_sellers > 0 && (
                              <b className="automation-failed"> · {automation.last_run.failed_sellers} с ошибкой</b>
                            )}
                            {durationLabel(automation.last_run.duration_seconds) &&
                              ` · ${durationLabel(automation.last_run.duration_seconds)}`}
                          </span>
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>Успешно завершалась</dt>
                      <dd>{momentLabel(automation.last_success_at, "ни разу")}</dd>
                    </div>
                    <div>
                      <dt>Следующий запуск</dt>
                      <dd>
                        {momentLabel(automation.next_run_at)}
                        <span className="automation-run-detail">за сутки запусков: {automation.runs_last_24h}</span>
                      </dd>
                    </div>
                  </dl>
                  <Link className="primary-button card-action" to={`/automations/${automation.id}`}>
                    Открыть <ArrowRight size={16} />
                  </Link>
                </article>
              );
            })}
            <article className="automation-card automation-placeholder" aria-label="Заглушка автоматизации">
              <div className="placeholder-visual" aria-hidden="true">
                <span className="placeholder-mark" />
                <span className="placeholder-line placeholder-line-wide" />
                <span className="placeholder-line" />
                <span className="placeholder-panel">
                  <i />
                  <i />
                  <i />
                </span>
              </div>
            </article>
          </section>
        )}
      </main>
    </div>
  );
}
