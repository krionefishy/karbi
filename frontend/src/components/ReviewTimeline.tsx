import { ChevronDown, ChevronLeft, ChevronRight, MessageSquare, Star, Triangle } from "lucide-react";
import { useMemo, useState } from "react";

import { averageRating, fiveStarsToTarget, pluralizeFives } from "../features/reviews/rating";
import type { ArticleState, DailyReviewSnapshot, ProductReviewHistory, RatingCounts } from "../features/reviews/types";

interface ReviewTimelineProps { products: ProductReviewHistory[]; }
interface Selection { productId: string; date: string; }

const HISTORY_DAYS = 90;
const RATINGS = [5, 4, 3, 2, 1] as const;

const stateLabels: Record<ArticleState, string> = {
  active: "В продаже",
  archived: "В архиве WB",
  feedback_only: "Нет в каталоге",
};

const dateFormatter = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", timeZone: "UTC" });
const weekdayFormatter = new Intl.DateTimeFormat("ru-RU", { weekday: "short", timeZone: "UTC" });

function totalOf(ratings: RatingCounts): number {
  return RATINGS.reduce((sum, rating) => sum + ratings[rating], 0);
}

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

function previousDay(date: string): string {
  const cursor = new Date(`${date}T00:00:00Z`);
  cursor.setUTCDate(cursor.getUTCDate() - 1);
  return cursor.toISOString().slice(0, 10);
}

function Delta({ value }: { value: number | null }) {
  if (value === null) return <span className="delta delta-neutral">—</span>;
  if (value === 0) return <span className="delta delta-neutral">— 0</span>;
  const positive = value > 0;
  return <span className={`delta ${positive ? "delta-positive" : "delta-negative"}`}><Triangle size={8} fill="currentColor" className={positive ? "" : "triangle-down"} />{positive ? "+" : "−"}{Math.abs(value)}</span>;
}

function RatingGoal({ label, ratings }: { label: string; ratings: RatingCounts }) {
  const average = averageRating(ratings);
  if (average === null) return null;
  const needed = fiveStarsToTarget(ratings);
  return (
    <div className="rating-goal-row">
      <span className="rating-goal-label">{label}</span>
      <b className="rating-goal-value">{average.toFixed(2)}</b>
      {needed === 0 ? (
        <span className="rating-goal-done">оценка 5,0 достигнута</span>
      ) : (
        <span>
          до 5,0 — ещё <b>{needed.toLocaleString("ru-RU")}</b> {pluralizeFives(needed)}
        </span>
      )}
    </div>
  );
}

function RatingDetails({ snapshot, card }: { snapshot: DailyReviewSnapshot; card?: DailyReviewSnapshot }) {
  const total = totalOf(snapshot.ratings);
  const cardTotal = card ? totalOf(card.ratings) : null;
  return (
    <div className="rating-details">
      <div className="detail-header">
        <span className="detail-date">Детали за {dateFormatter.format(new Date(`${snapshot.date}T00:00:00Z`))}</span>
        <span>Всего отзывов: <b>{total.toLocaleString("ru-RU")}</b></span>
        {cardTotal !== null && cardTotal !== total && (
          <span className="detail-card">По карточке целиком: <b>{cardTotal.toLocaleString("ru-RU")}</b></span>
        )}
      </div>
      <div className="rating-bars">
        {RATINGS.map((rating) => {
          const value = snapshot.ratings[rating];
          return <div className="rating-row" key={rating}><span>{rating} <Star size={12} fill="currentColor" /></span><div className="rating-track"><i style={{ width: `${total ? (value / total) * 100 : 0}%` }} /></div><b>{value.toLocaleString("ru-RU")}</b></div>;
        })}
      </div>
      <div className="rating-goal">
        <RatingGoal label="По артикулу" ratings={snapshot.ratings} />
        {card && cardTotal !== total && <RatingGoal label="По карточке" ratings={card.ratings} />}
        <p className="rating-goal-note">
          Оценка снизу по нашим данным: WB часть отзывов из рейтинга исключает, поэтому у него до 5,0 обычно ближе.
        </p>
      </div>
    </div>
  );
}

function ProductIdentity({ product }: { product: ProductReviewHistory }) {
  return (
    <div className="product-identity">
      {product.photo_url
        ? <img className="product-photo" src={product.photo_url} alt="" loading="lazy" />
        : <span className="product-photo product-photo-empty" aria-hidden="true" />}
      <div className="product-identity-text">
        <strong>{product.name}</strong>
        <span>
          WB{" "}
          <a href={`https://www.wildberries.ru/catalog/${product.article}/detail.aspx`} target="_blank" rel="noreferrer">
            {product.article}
          </a>
          {product.vendor_code && <> · продавца {product.vendor_code}</>}
          {product.brand && <> · {product.brand}</>}
        </span>
        <span className="product-tags">
          <span className={`product-state product-state-${product.state}`}>{stateLabels[product.state]}</span>
          {product.imt_id !== null && <span className="product-card-id">карточка {product.imt_id}</span>}
        </span>
      </div>
    </div>
  );
}

export function ReviewTimeline({ products }: ReviewTimelineProps) {
  const today = moscowToday();
  const historyStart = new Date(`${today}T00:00:00Z`);
  historyStart.setUTCDate(historyStart.getUTCDate() - (HISTORY_DAYS - 1));
  const dates = calendarDates(historyStart.toISOString().slice(0, 10), today);
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
        <span className="five-star-key"><MessageSquare size={13} /> Основное значение: всего отзывов по артикулу</span>
      </div>

      <div className="timeline-table">
        <div className="timeline-grid timeline-head">
          <div className="product-head">Товар и артикул</div>
          {range.map((date) => <div className={date === today ? "today-cell" : ""} key={date}><span>{dateFormatter.format(new Date(`${date}T00:00:00Z`))}</span><b>{date === today ? "Сегодня" : weekdayFormatter.format(new Date(`${date}T00:00:00Z`))}</b></div>)}
        </div>
        {products.map((product) => {
          const snapshots = new Map(product.snapshots.map((snapshot) => [snapshot.date, snapshot]));
          const cardSnapshots = new Map(product.card_snapshots.map((snapshot) => [snapshot.date, snapshot]));
          const selectedSnapshot = selection?.productId === product.id ? snapshots.get(selection.date) : undefined;
          return <div className="product-block" key={product.id}>
            <div className="timeline-grid product-row">
              <ProductIdentity product={product} />
              {range.map((date) => {
                const snapshot = snapshots.get(date);
                if (!snapshot) return <div className={`day-cell day-empty ${date === today ? "today-cell" : ""}`} key={date}>Нет данных</div>;
                const previous = snapshots.get(previousDay(date));
                const total = totalOf(snapshot.ratings);
                // A missing previous day is not a zero — leave the delta blank
                // so a gap in the series never reads as "reviews disappeared".
                const delta = previous ? total - totalOf(previous.ratings) : null;
                const selected = selection?.productId === product.id && selection.date === snapshot.date;
                return <button className={`day-cell ${date === today ? "today-cell" : ""} ${selected ? "day-selected" : ""}`} onClick={() => setSelection(selected ? null : { productId: product.id, date: snapshot.date })} key={snapshot.date} aria-expanded={selected}>
                  <span className="review-count"><MessageSquare size={11} />{total.toLocaleString("ru-RU")}</span><Delta value={delta} /><ChevronDown size={12} className="expand-chevron" />
                </button>;
              })}
            </div>
            {selectedSnapshot && <RatingDetails snapshot={selectedSnapshot} card={cardSnapshots.get(selectedSnapshot.date)} />}
          </div>;
        })}
      </div>
    </section>
  );
}
