import { percent } from "../features/reviewChats/format";
import type { DaySummary, GroupSummary } from "../features/reviewChats/types";

const dayFormatter = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", weekday: "short" });

const TEMPLATE = ["130px", "104px", "104px", "112px", "104px", "104px", "104px", "112px", "120px", "112px"].join(" ");

function rate(summary: GroupSummary) {
  return (
    <span className="stocks-cell chats-rate">
      {percent(summary.reply_rate)}
      {summary.pending > 0 && <em title="Окно ответа ещё не истекло: доля может только вырасти">ждём {summary.pending}</em>}
    </span>
  );
}

function count(value: number, muted = false) {
  return <span className={muted ? "stocks-cell chats-muted" : "stocks-cell"}>{value || "—"}</span>;
}

/**
 * День — по дате автосообщения WB: ответ следующим утром относится ко дню обращения.
 * Первый блок — все диалоги дня, это итог для покупателя; дальше — с нашим сообщением
 * и два счётчика, куда ушли остальные диалоги после запуска.
 */
export function ReviewChatsDays({ days }: { days: DaySummary[] }) {
  return (
    <section className="checklist-scroll chats-days" style={{ "--stocks-template": TEMPLATE } as React.CSSProperties}>
      <div className="stocks-head">
        <span className="checklist-sticky">День</span>
        <span>Все диалоги</span>
        <span>ответили</span>
        <span>% ответивших</span>
        <span>С нашим сообщ.</span>
        <span>ответили</span>
        <span>не ответили</span>
        <span>% ответивших</span>
        <span title="Покупатель написал раньше, чем ушло наше сообщение">Ответили раньше нашего</span>
        <span title="После запуска: наше сообщение не ушло, покупатель молчит">Пропуск рассылки</span>
      </div>
      {days.map((day) => (
        <div className="stocks-row" key={day.day}>
          <span className="checklist-sticky">{dayFormatter.format(new Date(`${day.day}T00:00:00`))}</span>
          {count(day.total.total)}
          {count(day.total.replied)}
          {rate(day.total)}
          {count(day.followed.total)}
          {count(day.followed.replied)}
          {count(day.followed.silent)}
          {day.followed.total ? rate(day.followed) : <span className="stocks-cell chats-muted">—</span>}
          {count(day.early.total, true)}
          {count(day.missed.total, true)}
        </div>
      ))}
      {days.length === 0 && <span className="fbs-row-empty">За этот период WB не открывал диалогов после отзывов.</span>}
    </section>
  );
}
