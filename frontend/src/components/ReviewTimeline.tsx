import { ChevronDown, ChevronLeft, ChevronRight, Star, Triangle } from "lucide-react";
import { useMemo, useState } from "react";

import type { DailyReviewSnapshot, ProductReviewHistory } from "../features/reviews/types";

interface ReviewTimelineProps { products: ProductReviewHistory[]; }
interface Selection { productId: string; date: string; }

const dateFormatter = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", timeZone: "UTC" });
const weekdayFormatter = new Intl.DateTimeFormat("ru-RU", { weekday: "short", timeZone: "UTC" });

function Delta({ value }: { value: number }) {
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
          return <div className="rating-row" key={rating}><span>{rating} <Star size={12} fill="currentColor" /></span><div className="rating-track"><i style={{ width: `${(value / total) * 100}%` }} /></div><b>{value.toLocaleString("ru-RU")}</b></div>;
        })}
      </div>
    </div>
  );
}

export function ReviewTimeline({ products }: ReviewTimelineProps) {
  const snapshotCount = products[0]?.snapshots.length ?? 0;
  const maxOffset = Math.max(0, snapshotCount - 7);
  const [offset, setOffset] = useState(0);
  const [selection, setSelection] = useState<Selection | null>(null);
  const range = useMemo(() => {
    const end = snapshotCount - offset;
    return products[0]?.snapshots.slice(Math.max(0, end - 7), end) ?? [];
  }, [offset, products, snapshotCount]);

  const startIndex = products[0] ? products[0].snapshots.findIndex((snapshot) => snapshot.date === range[0]?.date) : 0;
  const currentVisible = offset === 0;
  return (
    <section className="timeline-section">
      <div className="timeline-toolbar">
        <div className="window-controls">
          <button className="icon-button" onClick={() => setOffset((value) => Math.min(maxOffset, value + 7))} disabled={offset >= maxOffset} aria-label="Предыдущие семь дней"><ChevronLeft size={18} /></button>
          <button className="icon-button" onClick={() => setOffset((value) => Math.max(0, value - 7))} disabled={offset === 0} aria-label="Следующие семь дней"><ChevronRight size={18} /></button>
          <div><span className="eyebrow">Окно просмотра</span><strong className="range-title">{range[0] && dateFormatter.format(new Date(`${range[0].date}T00:00:00Z`))} — {range.at(-1) && dateFormatter.format(new Date(`${range.at(-1)!.date}T00:00:00Z`))}</strong></div>
        </div>
        <label className="history-slider"><span className="field-label">Глубина истории</span><input type="range" min="0" max={maxOffset} value={maxOffset - offset} onChange={(event) => setOffset(maxOffset - Number(event.target.value))} /></label>
        <span className="five-star-key"><Star size={13} fill="currentColor" /> Основное значение: 5 звёзд</span>
      </div>

      <div className="timeline-table">
        <div className="timeline-grid timeline-head">
          <div className="product-head">Товар и артикул</div>
          {range.map((snapshot, index) => <div className={currentVisible && index === range.length - 1 ? "today-cell" : ""} key={snapshot.date}><span>{dateFormatter.format(new Date(`${snapshot.date}T00:00:00Z`))}</span><b>{currentVisible && index === range.length - 1 ? "Сегодня" : weekdayFormatter.format(new Date(`${snapshot.date}T00:00:00Z`))}</b></div>)}
        </div>
        {products.map((product) => {
          const visible = product.snapshots.slice(startIndex, startIndex + range.length);
          const selectedSnapshot = selection?.productId === product.id ? product.snapshots.find((snapshot) => snapshot.date === selection.date) : undefined;
          return <div className="product-block" key={product.id}>
            <div className="timeline-grid product-row">
              <div className="product-identity"><strong>{product.name}</strong><span>арт. {product.article}</span></div>
              {visible.map((snapshot, index) => {
                const absoluteIndex = startIndex + index;
                const previous = product.snapshots[absoluteIndex - 1];
                const delta = previous ? snapshot.ratings[5] - previous.ratings[5] : 0;
                const selected = selection?.productId === product.id && selection.date === snapshot.date;
                return <button className={`day-cell ${currentVisible && index === visible.length - 1 ? "today-cell" : ""} ${selected ? "day-selected" : ""}`} onClick={() => setSelection(selected ? null : { productId: product.id, date: snapshot.date })} key={snapshot.date} aria-expanded={selected}>
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
