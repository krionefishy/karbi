import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BarChart3, Boxes, MessageSquareText } from "lucide-react";
import { Link } from "react-router-dom";

import { AppHeader } from "../components/AppHeader";
import { getAutomations } from "../features/automations/api";

const icons = [MessageSquareText, BarChart3, Boxes];

export function AutomationsPage() {
  const { data = [], isLoading } = useQuery({ queryKey: ["automations"], queryFn: getAutomations });
  return (
    <div className="app-page">
      <AppHeader />
      <main className="page-container automations-page">
        <div className="page-heading">
          <div><p className="eyebrow">Рабочее пространство</p><h1>Автоматизации</h1><p className="muted">Выберите процесс, с данными которого хотите работать.</p></div>
          <span className="system-status"><i /> Все системы работают</span>
        </div>
        {isLoading ? <div className="loading-block">Загружаем автоматизации…</div> : (
          <section className="automation-grid" aria-label="Доступные автоматизации">
            {data.map((automation, index) => {
              const Icon = icons[index] ?? Boxes;
              const active = automation.status === "active";
              return (
                <article className={`automation-card ${active ? "automation-active" : "automation-disabled"}`} key={automation.id}>
                  <div className="automation-card-top">
                    <span className="automation-icon"><Icon size={22} /></span>
                    <span className={`status-badge ${active ? "status-active" : "status-soon"}`}><span />{active ? "Активна" : "Скоро"}</span>
                  </div>
                  <h2>{automation.title}</h2>
                  <p className="muted">{automation.description}</p>
                  <div className="automation-meta">
                    {active ? <><span><b>{automation.seller_count}</b> селлера</span><span>Последний запуск <b>сегодня, 06:10</b></span></> : <span>Автоматизация находится в плане разработки</span>}
                  </div>
                  {active ? <Link className="primary-button card-action" to="/automations/wb-reviews">Открыть <ArrowRight size={16} /></Link> : <button className="secondary-button card-action" disabled>Недоступно</button>}
                </article>
              );
            })}
          </section>
        )}
      </main>
    </div>
  );
}
