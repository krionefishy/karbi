import { ChevronDown, ChevronLeft, ChevronRight, Star, Triangle } from "lucide-react";
import { useMemo, useState } from "react";

import type { DailyReviewSnapshot, ProductReviewHistory } from "../features/reviews/types";

interface ReviewTimelineProps { products: ProductReviewHistory[]; }
interface Selection { productId: string; date: string; }

const dateFormatter = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", timeZone: "UTC" });
const weekdayFormatter = new Intl.DateTimeFormat("ru-RU", { weekday: "short", timeZone: "UTC" });

function moscowToday(): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    timeZone: "Europe/Moscow",
  }).formatToParts(new Date());
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
}

function calendarDates(from: string, to: string): string[] {
  const dates: string[] = [];
  const cursor = new Date(`${from}T00:00:00Z`);
  const end = new Date(`${to}T00:00:00Z`);
  while (cursor <= end) {
    dates.push(cursor.toISOString().slice(0, 10));
    cursor.setUTCDate(cursor.getUTCDate() + 1);
  }
  return dates;
}

function Delta({ value }: { value: number | null }) {
  if (value === null) return <span className="delta delta-neutral">—</span>;
  if (value === 0) return <span className="delta delta-neutral">— 0</span>;
  const positive = value > 0;
  return <span className={`delta ${positive ? "delta-positive" : "delta-negative"}`}><Triangle size={8} fill="currentColor" className={positive ? "" : "triangle-down"} />{positive ? "+" : "−"}{Math.abs(value)}</span>;
}

function RatingDetails({ snapshot }: { snapshot: DailyReviewSnapshot }) {
  const total = Object.values(snapshot.ratings).reduce((sum, count) => sum + count, 0);
  return (
    <div className="rating-details">
      <div className="detail-header"><span className="detail-date">Детали за {dateFormatter.format(new Date(`${snapshot.date}T00:00:00Z`))}</span><span>Всего отзывов: <b>{total.toLocaleString("ru-RU")}</b></span></div>
      <div className="rating-bars">
        {([5, 4, 3, 2, 1] as const).map((rating) => {
          const value = snapshot.ratings[rating];
          return <div className="rating-row" key={rating}><span>{rating} <Star size={12} fill="currentColor" /></span><div className="rating-track"><i style={{ width: `${total ? (value / total) * 100 : 0}%` }} /></div><b>{value.toLocaleString("ru-RU")}</b></div>;
        })}
      </div>
    </div>
  );
}

export function ReviewTimeline({ products }: ReviewTimelineProps) {
  const today = moscowToday();
  const earliestSnapshot = products
    .flatMap((product) => product.snapshots.map((snapshot) => snapshot.date))
    .sort()[0];
  const fallbackStart = new Date(`${today}T00:00:00Z`);
  fallbackStart.setUTCDate(fallbackStart.getUTCDate() - 6);
  const dates = calendarDates(earliestSnapshot ?? fallbackStart.toISOString().slice(0, 10), today);
  const maxOffset = Math.max(0, dates.length - 7);
  const [offset, setOffset] = useState(0);
  const [selection, setSelection] = useState<Selection | null>(null);
  const range = useMemo(() => {
    const end = dates.length - offset;
    return dates.slice(Math.max(0, end - 7), end);
  }, [dates, offset]);

  return (
    <section className="timeline-section">
      <div className="timeline-toolbar">
        <div className="window-controls">
          <button className="icon-button" onClick={() => setOffset((value) => Math.min(maxOffset, value + 7))} disabled={offset >= maxOffset} aria-label="Предыдущие семь дней"><ChevronLeft size={18} /></button>
          <button className="icon-button" onClick={() => setOffset((value) => Math.max(0, value - 7))} disabled={offset === 0} aria-label="Следующие семь дней"><ChevronRight size={18} /></button>
          <div><span className="eyebrow">Окно просмотра</span><strong className="range-title">{range[0] && dateFormatter.format(new Date(`${range[0]}T00:00:00Z`))} — {range.at(-1) && dateFormatter.format(new Date(`${range.at(-1)}T00:00:00Z`))}</strong></div>
        </div>
        <label className="history-slider"><span className="field-label">Глубина истории</span><input type="range" min="0" max={maxOffset} value={maxOffset - offset} onChange={(event) => setOffset(maxOffset - Number(event.target.value))} /></label>
        <span className="five-star-key"><Star size={13} fill="currentColor" /> Основное значение: 5 звёзд</span>
      </div>

      <div className="timeline-table">
        <div className="timeline-grid timeline-head">
          <div className="product-head">Товар и артикул</div>
          {range.map((date) => <div className={date === today ? "today-cell" : ""} key={date}><span>{dateFormatter.format(new Date(`${date}T00:00:00Z`))}</span><b>{date === today ? "Сегодня" : weekdayFormatter.format(new Date(`${date}T00:00:00Z`))}</b></div>)}
        </div>
        {products.map((product) => {
          const snapshots = new Map(product.snapshots.map((snapshot) => [snapshot.date, snapshot]));
          const selectedSnapshot = selection?.productId === product.id ? product.snapshots.find((snapshot) => snapshot.date === selection.date) : undefined;
          return <div className="product-block" key={product.id}>
            <div className="timeline-grid product-row">
              <div className="product-identity"><strong>{product.name}</strong><span>арт. {product.article}</span></div>
              {range.map((date) => {
                const snapshot = snapshots.get(date);
                if (!snapshot) return <div className={`day-cell day-empty ${date === today ? "today-cell" : ""}`} key={date}>—</div>;
                const previousDate = new Date(`${date}T00:00:00Z`);
                previousDate.setUTCDate(previousDate.getUTCDate() - 1);
                const previous = snapshots.get(previousDate.toISOString().slice(0, 10));
                const delta = previous ? snapshot.ratings[5] - previous.ratings[5] : null;
                const selected = selection?.productId === product.id && selection.date === snapshot.date;
                return <button className={`day-cell ${date === today ? "today-cell" : ""} ${selected ? "day-selected" : ""}`} onClick={() => setSelection(selected ? null : { productId: product.id, date: snapshot.date })} key={snapshot.date} aria-expanded={selected}>
                  <span className="review-count"><Star size={11} fill="currentColor" />{snapshot.ratings[5].toLocaleString("ru-RU")}</span><Delta value={delta} /><ChevronDown size={12} className="expand-chevron" />
                </button>;
              })}
            </div>
            {selectedSnapshot && <RatingDetails snapshot={selectedSnapshot} />}
          </div>;
        })}
      </div>
    </section>
  );
}
