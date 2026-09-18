import { percent } from "../features/reviewChats/format";
import type { DaySummary, GroupSummary } from "../features/reviewChats/types";

const dayFormatter = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", weekday: "short" });

const TEMPLATE = ["130px", ...Array(8).fill("112px")].join(" ");

function cells(summary: GroupSummary, muted: boolean) {
  const tone = muted ? "stocks-cell chats-muted" : "stocks-cell";
  return (
    <>
      <span className={tone}>{summary.total || "—"}</span>
      <span className={tone}>{summary.total ? summary.replied : "—"}</span>
      <span className={tone}>{summary.total ? summary.silent : "—"}</span>
      <span className={`${tone} chats-rate`}>
        {percent(summary.reply_rate)}
        {summary.pending > 0 && <em title="Окно ответа ещё не истекло">ждём {summary.pending}</em>}
      </span>
    </>
  );
}

/** День — по дате автосообщения WB: ответ следующим утром относится ко дню обращения. */
export function ReviewChatsDays({ days }: { days: DaySummary[] }) {
  return (
    <section className="checklist-scroll chats-days" style={{ "--stocks-template": TEMPLATE } as React.CSSProperties}>
      <div className="stocks-head">
        <span className="checklist-sticky">День</span>
        <span>С нашим · диалогов</span>
        <span>ответили</span>
        <span>не ответили</span>
        <span>% ответивших</span>
        <span>Без нашего · диалогов</span>
        <span>ответили</span>
        <span>не ответили</span>
        <span>% ответивших</span>
      </div>
      {days.map((day) => (
        <div className="stocks-row" key={day.day}>
          <span className="checklist-sticky">{dayFormatter.format(new Date(`${day.day}T00:00:00`))}</span>
          {cells(day.followed, false)}
          {cells(day.bare, true)}
        </div>
      ))}
      {days.length === 0 && <span className="fbs-row-empty">За этот период WB не открывал диалогов после отзывов.</span>}
    </section>
  );
}
