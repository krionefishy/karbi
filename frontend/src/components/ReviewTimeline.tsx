import { BarChart2, ChevronDown, ChevronLeft, ChevronRight, MessageSquare, Star, Triangle } from "lucide-react";
import { useMemo, useState } from "react";

import { CHART_DAYS } from "../features/reviews/chart";
import { daysBetween, moscowToday, previousDay, shiftDay } from "../features/reviews/days";
import { movementOn } from "../features/reviews/movement";
import { averageRating, fiveStarsToTarget, pluralizeFives } from "../features/reviews/rating";
import type { DailyReviewSnapshot, ProductReviewHistory, RatingCounts } from "../features/reviews/types";
import type { ArticleState } from "../features/sellers/types";
import { ProductDetails } from "./ProductDetails";
import { ReviewChart } from "./ReviewChart";

interface ReviewTimelineProps { products: ProductReviewHistory[]; latestDate?: string | null; }

/** One panel is open at a time: either a day's breakdown or the product's chart. */
type Selection =
  | { kind: "day"; productId: string; date: string }
  | { kind: "chart"; productId: string };

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

function MovementBadge({ delta }: { delta: number }) {
  const positive = delta > 0;
  return (
    <span className={`movement-badge movement-${positive ? "up" : "down"}`}>
      <Triangle size={8} fill="currentColor" className={positive ? "" : "triangle-down"} />
      {positive ? "+" : "−"}
      {Math.abs(delta).toLocaleString("ru-RU")} за сутки
    </span>
  );
}

interface ProductIdentityProps {
  product: ProductReviewHistory;
  delta: number | null;
  open: boolean;
  onToggle: () => void;
}

function ProductIdentity({ product, delta, open, onToggle }: ProductIdentityProps) {
  return (
    <div className={`product-identity product-identity-${product.state}`}>
      <div className="product-identity-text">
        <strong title={product.name}>{product.name}</strong>
        <span className="product-identity-ids">
          WB{" "}
          <a href={`https://www.wildberries.ru/catalog/${product.article}/detail.aspx`} target="_blank" rel="noreferrer">
            {product.article}
          </a>
          {product.vendor_code && <> · {product.vendor_code}</>}
        </span>
        <span className="product-tags">
          <span className={`product-state product-state-${product.state}`}>{stateLabels[product.state]}</span>
          {product.imt_id !== null && <span className="product-card-id">карточка {product.imt_id}</span>}
        </span>
      </div>
      {delta !== null && delta !== 0 && <MovementBadge delta={delta} />}
      <button
        type="button"
        className={`chart-toggle${open ? " chart-toggle-open" : ""}`}
        onClick={onToggle}
        aria-expanded={open}
        aria-label={`Карточка и динамика за ${CHART_DAYS} дней: ${product.name}`}
        title={`Карточка товара и динамика за ${CHART_DAYS} дней`}
      >
        <BarChart2 size={15} />
      </button>
    </div>
  );
}

export function ReviewTimeline({ products, latestDate = null }: ReviewTimelineProps) {
  const today = moscowToday();
  const dates = daysBetween(shiftDay(today, -(HISTORY_DAYS - 1)), today);
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
          const openHere = selection?.productId === product.id ? selection : null;
          const selectedSnapshot = openHere?.kind === "day" ? snapshots.get(openHere.date) : undefined;
          const chartOpen = openHere?.kind === "chart";
          return <div className="product-block" key={product.id}>
            <div className="timeline-grid product-row">
              <ProductIdentity
                product={product}
                delta={movementOn(product, latestDate).delta}
                open={chartOpen}
                onToggle={() => setSelection(chartOpen ? null : { kind: "chart", productId: product.id })}
              />
              {range.map((date) => {
                const snapshot = snapshots.get(date);
                if (!snapshot) return <div className={`day-cell day-empty ${date === today ? "today-cell" : ""}`} key={date}>Нет данных</div>;
                const previous = snapshots.get(previousDay(date));
                const total = totalOf(snapshot.ratings);
                // A missing previous day is not a zero — leave the delta blank
                // so a gap in the series never reads as "reviews disappeared".
                const delta = previous ? total - totalOf(previous.ratings) : null;
                const selected = openHere?.kind === "day" && openHere.date === snapshot.date;
                return <button className={`day-cell ${date === today ? "today-cell" : ""} ${selected ? "day-selected" : ""}`} onClick={() => setSelection(selected ? null : { kind: "day", productId: product.id, date: snapshot.date })} key={snapshot.date} aria-expanded={selected} aria-label={`Отзывы за ${dateFormatter.format(new Date(`${date}T00:00:00Z`))}`}>
                  <span className="review-count"><MessageSquare size={11} />{total.toLocaleString("ru-RU")}</span><Delta value={delta} /><ChevronDown size={12} className="expand-chevron" />
                </button>;
              })}
            </div>
            {chartOpen && (
              <div className="product-panel">
                <ProductDetails product={product} stateLabel={stateLabels} />
                <ReviewChart snapshots={product.snapshots} cardSnapshots={product.card_snapshots} endDate={today} />
              </div>
            )}
            {selectedSnapshot && <RatingDetails snapshot={selectedSnapshot} card={cardSnapshots.get(selectedSnapshot.date)} />}
          </div>;
        })}
      </div>
    </section>
  );
}
