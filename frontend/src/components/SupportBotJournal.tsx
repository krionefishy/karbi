import { ChevronLeft, ChevronRight, ExternalLink } from "lucide-react";

import { SEND_LABEL } from "../features/supportBot/format";
import type { JournalFilter, JournalStatus, SendPage } from "../features/supportBot/types";

const momentFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
});
const dayFormatter = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit" });

const FILTERS: { value: JournalStatus | ""; label: string }[] = [
  { value: "", label: "Все" },
  { value: "sent", label: "Отправлено" },
  { value: "dry_run", label: "Сухой прогон" },
  { value: "failed", label: "Не ушло" },
  { value: "unknown", label: "Неизвестно" },
];

interface Props {
  page: SendPage | undefined;
  filter: JournalFilter;
  onFilter: (filter: JournalFilter) => void;
}

function total(counts: SendPage["counts"]): number {
  return Object.values(counts).reduce((sum, value) => sum + (value ?? 0), 0);
}

export function SupportBotJournal({ page, filter, onFilter }: Props) {
  const counts = page?.counts ?? {};
  const countOf = (value: JournalStatus | "") => (value === "" ? total(counts) : (counts[value] ?? 0));

  return (
    <div className="bot-journal">
      <div className="bot-pills" role="tablist" aria-label="Статус отправки">
        {FILTERS.map((option) => (
          <button
            key={option.value || "all"}
            type="button"
            role="tab"
            aria-selected={filter.status === option.value}
            className={filter.status === option.value ? "bot-pill bot-pill-on" : "bot-pill"}
            onClick={() => onFilter({ status: option.value, page: 1 })}
          >
            {option.label} · {countOf(option.value)}
          </button>
        ))}
      </div>

      <section className="bot-journal-table" aria-label="Журнал отправок">
        <div className="bot-journal-row bot-journal-head">
          <span>Когда</span>
          <span>Диалог</span>
          <span>Статус</span>
          <span>Текст</span>
        </div>
        {page?.sends.map((item) => (
          <div className="bot-journal-row" key={item.id}>
            <span className="bot-journal-when">{momentFormatter.format(new Date(item.sent_at ?? item.planned_at))}</span>
            <span className="bot-journal-dialog">
              {item.chat_url ? (
                <a className="wb-article-link" href={item.chat_url} target="_blank" rel="noreferrer">
                  {dayFormatter.format(new Date(item.prompt_at))}
                  {item.nm_id ? ` · арт. ${item.nm_id}` : ""} <ExternalLink size={12} aria-hidden="true" />
                </a>
              ) : (
                <span title={item.chat_id}>
                  {dayFormatter.format(new Date(item.prompt_at))}
                  {item.nm_id ? ` · арт. ${item.nm_id}` : ""}
                </span>
              )}
            </span>
            <span>
              <span className={`bot-badge bot-badge-${item.status}`}>{SEND_LABEL[item.status]}</span>
            </span>
            <span className="bot-journal-text" title={item.text}>
              {item.error ? <em className="bot-journal-error">{item.error}</em> : item.text}
            </span>
          </div>
        ))}
        {page && page.sends.length === 0 && (
          <span className="fbs-row-empty">
            {filter.status ? "Под этот статус отправок нет." : "Отправок ещё не было."}
          </span>
        )}
      </section>

      {page && page.total > page.page_size && (
        <nav className="penalties-pager" aria-label="Страницы журнала">
          <button
            type="button"
            className="secondary-button"
            disabled={filter.page <= 1}
            onClick={() => onFilter({ ...filter, page: filter.page - 1 })}
          >
            <ChevronLeft size={15} /> Назад
          </button>
          <span>
            {(page.page - 1) * page.page_size + 1}–{Math.min(page.page * page.page_size, page.total)} из {page.total}
          </span>
          <button
            type="button"
            className="secondary-button"
            disabled={page.page * page.page_size >= page.total}
            onClick={() => onFilter({ ...filter, page: filter.page + 1 })}
          >
            Вперёд <ChevronRight size={15} />
          </button>
        </nav>
      )}
    </div>
  );
}
