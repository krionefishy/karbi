import { useQuery } from "@tanstack/react-query";
import { ArrowRight, MessageSquareText } from "lucide-react";
import { Link } from "react-router-dom";

import { AppHeader } from "../components/AppHeader";
import { getAutomations, getPlatformReadiness } from "../features/automations/api";

function sellerLabel(count: number | null) {
  const value = count ?? 0;
  if (value % 10 === 1 && value % 100 !== 11) return `${value} селлер`;
  if ([2, 3, 4].includes(value % 10) && ![12, 13, 14].includes(value % 100)) return `${value} селлера`;
  return `${value} селлеров`;
}

function lastRunLabel(value: string | null) {
  return value
    ? new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value))
    : "ещё не запускалась";
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
                      <MessageSquareText size={22} />
                    </span>
                    <span className="status-badge status-active">
                      <span />
                      Активна
                    </span>
                  </div>
                  <h2>{automation.title}</h2>
                  <p className="muted">{automation.description}</p>
                  <div className="automation-meta">
                    <span>
                      <b>{sellerLabel(automation.seller_count)}</b>
                    </span>
                    <span>
                      Последний запуск <b>{lastRunLabel(automation.last_run_at)}</b>
                    </span>
                  </div>
                  <Link className="primary-button card-action" to="/automations/wb-reviews">
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
