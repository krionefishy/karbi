import { Link } from "react-router-dom";

import { Shell } from "../components/Shell";

export function ServiceDeniedPage() {
  return (
    <Shell>
      <main className="page-container">
        <section className="empty-state" role="alert">
          <h1>Нет доступа к сервису</h1>
          <p className="muted">Этот сервис вам не выдан. Если он нужен для работы, попросите администратора.</p>
          <Link className="secondary-button" to="/automations">
            К доступным сервисам
          </Link>
        </section>
      </main>
    </Shell>
  );
}
