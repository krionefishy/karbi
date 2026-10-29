import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { QUEUE_KEY, ScreenshotAdminQueue } from "../../components/ScreenshotAdminQueue";
import { ScreenshotAdminManagers } from "../../components/ScreenshotAdminManagers";
import { ScreenshotAdminPipeline } from "../../components/ScreenshotAdminPipeline";
import { Shell } from "../../components/Shell";
import type { QueueTab } from "../../features/screenshotControl/admin";
import { getQueue } from "../../features/screenshotControl/api";

const TITLE = "Контроль скриншотов ДБС";

type Tab = QueueTab | "managers" | "pipeline";

const TABS: { key: Tab; label: string; count?: QueueTab }[] = [
  { key: "review", label: "Проверка", count: "review" },
  { key: "duplicates", label: "Подозрения", count: "duplicates" },
  { key: "audit", label: "Аудит", count: "audit" },
  { key: "managers", label: "Менеджеры" },
  { key: "pipeline", label: "Конвейер" },
];

const SUBTITLE: Record<Tab, string> = {
  review: "Спорные скриншоты: решение за вами, конвейер сам ничего не засчитывает",
  duplicates: "Дубли и совпадения с чужими скриншотами",
  audit: "Случайные принятые скриншоты для перепроверки",
  managers: "Доли дублей, ревью и всплески по каждому менеджеру",
  pipeline: "Очередь распознавания, задержки, ошибки и хранилище",
};

export function ScreenshotsPage() {
  const [tab, setTab] = useState<Tab>("review");
  // Счётчики вкладок приходят с любой страницы очереди; берём самую лёгкую.
  const counts = useQuery({
    queryKey: [QUEUE_KEY, "counts"],
    queryFn: async () => (await getQueue({ kind: "review", limit: 1 })).counts,
    refetchInterval: 30_000,
  });

  return (
    <Shell admin current={TITLE}>
      <main className="page-container sca-page">
        <div className="page-heading">
          <div>
            <h1>{TITLE}</h1>
            <p className="muted">{SUBTITLE[tab]}</p>
          </div>
        </div>

        <div className="mode-switch" role="tablist" aria-label="Разделы">
          {TABS.map((item) => (
            <button
              key={item.key}
              type="button"
              role="tab"
              id={`sca-tab-${item.key}`}
              aria-controls="sca-tabpanel"
              aria-selected={tab === item.key}
              className={tab === item.key ? "mode-active" : undefined}
              onClick={() => setTab(item.key)}
            >
              {item.label}
              {item.count && counts.data && <small>{counts.data[item.count]}</small>}
            </button>
          ))}
        </div>

        <div role="tabpanel" id="sca-tabpanel" aria-labelledby={`sca-tab-${tab}`}>
          {tab === "managers" ? (
            <ScreenshotAdminManagers />
          ) : tab === "pipeline" ? (
            <ScreenshotAdminPipeline />
          ) : (
            <ScreenshotAdminQueue key={tab} kind={tab} />
          )}
        </div>
      </main>
    </Shell>
  );
}
