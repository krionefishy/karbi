import {
  BarChart2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  MessageSquare,
  MoreHorizontal,
  Pin,
  Sigma,
  Star,
  Triangle,
} from "lucide-react";
import { type ReactNode, useEffect, useMemo, useState } from "react";

import { CHART_DAYS } from "../features/reviews/chart";
import { daysBetween, moscowToday, previousDay, shiftDay } from "../features/reviews/days";
import { collapsedSnapshots, isGroup, pluralizeDummies, type ProductGroup } from "../features/reviews/groups";
import { movementOn } from "../features/reviews/movement";
import { averageRating, fiveStarsToTarget, pluralizeFives } from "../features/reviews/rating";
import type { DailyReviewSnapshot, GroupAction, ProductReviewHistory, RatingCounts } from "../features/reviews/types";
import type { ArticleState } from "../features/sellers/types";
import { AttachToMainDialog } from "./AttachToMainDialog";
import { ProductDetails } from "./ProductDetails";
import { ReviewChart } from "./ReviewChart";

interface ReviewTimelineProps {
  groups: ProductGroup[];
  latestDate?: string | null;
  /** Группы, раскрытые фильтром: под него попали только болванки. */
  revealed?: ReadonlySet<string>;
  /** Основные товары, к которым можно привязать болванку (по всему селлеру, а не только видимые). */
  attachTargets?: ProductReviewHistory[];
  onGroupAction?: (article: string, action: GroupAction, mainArticle?: string) => void;
  groupError?: string;
}

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

function RatingDetails({ snapshot, card, grouped = false }: { snapshot: DailyReviewSnapshot; card?: DailyReviewSnapshot; grouped?: boolean }) {
  const total = totalOf(snapshot.ratings);
  const cardTotal = card ? totalOf(card.ratings) : null;
  return (
    <div className="rating-details">
      <div className="detail-header">
        <span className="detail-date">Детали за {dateFormatter.format(new Date(`${snapshot.date}T00:00:00Z`))}</span>
        <span>{grouped ? "Всего по группе: " : "Всего отзывов: "}<b>{total.toLocaleString("ru-RU")}</b></span>
        {!grouped && cardTotal !== null && cardTotal !== total && (
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
        <RatingGoal label={grouped ? "По группе" : "По артикулу"} ratings={snapshot.ratings} />
        {!grouped && card && cardTotal !== total && <RatingGoal label="По карточке" ratings={card.ratings} />}
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

const ratingFormatter = new Intl.NumberFormat("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 2 });

const ratingTitles = {
  wb: "Рейтинг WB",
  group: "Рейтинг основного товара группы",
  reviews: "WB не отдал рейтинг, это средняя по нашим счётчикам отзывов",
} as const;

export function RatingBadge({ product }: { product: ProductReviewHistory }) {
  if (product.rating === null || product.rating_source === null) return null;
  const value = ratingFormatter.format(product.rating);
  const low = product.rating_source === "wb" && product.rating < 4.7;
  return (
    <span
      className={`rating-badge rating-badge-${product.rating_source}${low ? " rating-badge-low" : ""}`}
      title={ratingTitles[product.rating_source]}
    >
      {product.rating_source === "reviews" ? "≈" : <Star size={11} fill="currentColor" aria-hidden="true" />}
      {value}
    </span>
  );
}

interface ProductIdentityProps {
  product: ProductReviewHistory;
  group: ProductGroup;
  delta: number | null;
  open: boolean;
  onToggle: () => void;
  expanded: boolean;
  onExpand?: () => void;
  menuOpen: boolean;
  onMenu?: () => void;
  menu?: ReactNode;
}

function ProductIdentity({ product, group, delta, open, onToggle, expanded, onExpand, menuOpen, onMenu, menu }: ProductIdentityProps) {
  const isMain = group.main === product;
  const grouped = isGroup(group);
  const member = grouped && !isMain;
  return (
    <div className={`product-identity product-identity-${product.state}${member ? " product-identity-member" : ""}`}>
      {grouped && isMain && onExpand && (
        <button
          type="button"
          className={`group-toggle${expanded ? " group-toggle-open" : ""}`}
          onClick={onExpand}
          aria-expanded={expanded}
          aria-label={`${expanded ? "Свернуть" : "Показать"} болванки: ${product.name}`}
        >
          <ChevronRight size={14} />
        </button>
      )}
      <div className="product-identity-text">
        <strong className="product-identity-name" title={product.name}>{product.name}</strong>
        <span className="product-identity-ids">
          <RatingBadge product={product} />
          <span className="product-identity-codes">
            WB{" "}
            <a href={`https://www.wildberries.ru/catalog/${product.article}/detail.aspx`} target="_blank" rel="noreferrer">
              {product.article}
            </a>
            {product.vendor_code && <> · {product.vendor_code}</>}
          </span>
        </span>
        <span className="product-tags">
          {grouped && isMain ? (
            <>
              <span className="group-tag group-tag-main">
                {product.group_manual && <Pin size={10} aria-hidden="true" />}
                {product.group_manual ? "основной · закреплён" : "основной"}
              </span>
              <span className="group-tag">+{group.members.length} {pluralizeDummies(group.members.length)}</span>
            </>
          ) : (
            <span className={`product-state product-state-${product.state}`}>{stateLabels[product.state]}</span>
          )}
          {member && product.photo_count === 0 && <span className="group-tag group-tag-dummy">без фото</span>}
          {member && product.group_manual && <span className="group-tag-note">привязана вручную</span>}
          {!grouped && product.imt_id !== null && <span className="product-card-id">карточка {product.imt_id}</span>}
          {delta !== null && delta !== 0 && <MovementBadge delta={delta} />}
        </span>
      </div>
      <div className="product-identity-actions">
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
        {onMenu && (
          <button
            type="button"
            className={`row-menu-toggle${menuOpen ? " row-menu-toggle-open" : ""}`}
            onClick={(event) => {
              event.stopPropagation();
              onMenu();
            }}
            aria-expanded={menuOpen}
            aria-haspopup="menu"
            aria-label={`Группа товара ${product.article}`}
          >
            <MoreHorizontal size={15} />
          </button>
        )}
      </div>
      {menuOpen && menu}
    </div>
  );
}

interface GroupMenuProps {
  product: ProductReviewHistory;
  group: ProductGroup;
  onAction: (action: GroupAction) => void;
}

function GroupMenu({ product, group, onAction }: GroupMenuProps) {
  const isMain = group.main === product;
  const grouped = isGroup(group);
  const items: { action: GroupAction; label: string }[] = [];
  if (grouped && !isMain) items.push({ action: "pin_main", label: "Сделать основным в группе" });
  if (grouped && isMain && !product.group_manual) items.push({ action: "pin_main", label: "Закрепить как основной" });
  items.push({ action: "attach", label: "Привязать к основному товару…" });
  if (grouped && !isMain) items.push({ action: "detach", label: "Вывести из группы" });
  if (product.group_manual) items.push({ action: "reset", label: "Вернуть в группу по названию" });
  return (
    <div className="row-menu" role="menu" onClick={(event) => event.stopPropagation()}>
      <span className="row-menu-caption">WB {product.article}</span>
      {items.map((item) => (
        <button type="button" role="menuitem" key={item.action} onClick={() => onAction(item.action)}>
          {item.label}
        </button>
      ))}
    </div>
  );
}

function DayCells({
  product,
  snapshots,
  range,
  today,
  summed,
  selectedDate,
  onSelect,
}: {
  product: ProductReviewHistory;
  snapshots: Map<string, DailyReviewSnapshot>;
  range: string[];
  today: string;
  summed: boolean;
  selectedDate: string | null;
  onSelect: (date: string | null) => void;
}) {
  return (
    <>
      {range.map((date) => {
        const snapshot = snapshots.get(date);
        if (!snapshot) return <div className={`day-cell day-empty ${date === today ? "today-cell" : ""}`} key={date}>Нет данных</div>;
        const previous = snapshots.get(previousDay(date));
        const total = totalOf(snapshot.ratings);
        const delta = previous ? total - totalOf(previous.ratings) : null;
        const selected = selectedDate === snapshot.date;
        return <button className={`day-cell ${date === today ? "today-cell" : ""} ${selected ? "day-selected" : ""}`} onClick={() => onSelect(selected ? null : snapshot.date)} key={snapshot.date} aria-expanded={selected} aria-label={`${summed ? "Отзывы группы" : "Отзывы"} за ${dateFormatter.format(new Date(`${date}T00:00:00Z`))}: ${product.name}`}>
          <span className="review-count">{summed ? <Sigma size={11} className="group-sum" aria-label="сумма по группе" /> : <MessageSquare size={11} />}{total.toLocaleString("ru-RU")}</span><Delta value={delta} /><ChevronDown size={12} className="expand-chevron" />
        </button>;
      })}
    </>
  );
}

export function ReviewTimeline({
  groups,
  latestDate = null,
  revealed,
  attachTargets,
  onGroupAction,
  groupError,
}: ReviewTimelineProps) {
  const today = moscowToday();
  const dates = daysBetween(shiftDay(today, -(HISTORY_DAYS - 1)), today);
  const maxOffset = Math.max(0, dates.length - 7);
  const [offset, setOffset] = useState(0);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [attaching, setAttaching] = useState<ProductReviewHistory | null>(null);
  const range = useMemo(() => {
    const end = dates.length - offset;
    return dates.slice(Math.max(0, end - 7), end);
  }, [dates, offset]);
  const grouped = groups.filter(isGroup);
  const isOpen = (group: ProductGroup) => expanded.has(group.id) || Boolean(revealed?.has(group.id));
  const allOpen = grouped.length > 0 && grouped.every(isOpen);
  const targets = attachTargets ?? groups.map((group) => group.main);

  useEffect(() => {
    if (menuFor === null) return;
    const close = () => setMenuFor(null);
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && close();
    document.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuFor]);

  const toggleGroup = (id: string) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const runAction = (product: ProductReviewHistory, action: GroupAction) => {
    setMenuFor(null);
    if (action === "attach") setAttaching(product);
    else onGroupAction?.(product.article, action);
  };

  const renderRow = (product: ProductReviewHistory, group: ProductGroup, member: boolean) => {
    const groupRow = !member && isGroup(group);
    const summed = groupRow && !isOpen(group);
    const rowSnapshots = summed ? collapsedSnapshots(group) : product.snapshots;
    const snapshots = new Map(rowSnapshots.map((snapshot) => [snapshot.date, snapshot]));
    const cardSnapshots = new Map(product.card_snapshots.map((snapshot) => [snapshot.date, snapshot]));
    const openHere = selection?.productId === product.id ? selection : null;
    const selectedSnapshot = openHere?.kind === "day" ? snapshots.get(openHere.date) : undefined;
    const chartOpen = openHere?.kind === "chart";
    return <div className={`product-block${member ? " product-block-member" : ""}`} key={product.id}>
      <div className={`timeline-grid product-row${member ? " product-row-member" : ""}`}>
        <ProductIdentity
          product={product}
          group={group}
          delta={movementOn({ snapshots: rowSnapshots }, latestDate).delta}
          open={chartOpen}
          onToggle={() => setSelection(chartOpen ? null : { kind: "chart", productId: product.id })}
          expanded={isOpen(group)}
          onExpand={groupRow ? () => toggleGroup(group.id) : undefined}
          menuOpen={menuFor === product.id}
          onMenu={onGroupAction ? () => setMenuFor(menuFor === product.id ? null : product.id) : undefined}
          menu={<GroupMenu product={product} group={group} onAction={(action) => runAction(product, action)} />}
        />
        <DayCells
          product={product}
          snapshots={snapshots}
          range={range}
          today={today}
          summed={summed}
          selectedDate={openHere?.kind === "day" ? openHere.date : null}
          onSelect={(date) => setSelection(date === null ? null : { kind: "day", productId: product.id, date })}
        />
      </div>
      {chartOpen && (
        <div className="product-panel">
          <ProductDetails product={product} stateLabel={stateLabels} />
          <ReviewChart snapshots={product.snapshots} cardSnapshots={product.card_snapshots} endDate={today} />
        </div>
      )}
      {selectedSnapshot && <RatingDetails snapshot={selectedSnapshot} card={cardSnapshots.get(selectedSnapshot.date)} grouped={summed} />}
    </div>;
  };

  return (
    <section className="timeline-section">
      <div className="timeline-toolbar">
        <div className="window-controls">
          <button className="icon-button" onClick={() => setOffset((value) => Math.min(maxOffset, value + 7))} disabled={offset >= maxOffset} aria-label="Предыдущие семь дней"><ChevronLeft size={18} /></button>
          <button className="icon-button" onClick={() => setOffset((value) => Math.max(0, value - 7))} disabled={offset === 0} aria-label="Следующие семь дней"><ChevronRight size={18} /></button>
          <div><span className="eyebrow">Окно просмотра</span><strong className="range-title">{range[0] && dateFormatter.format(new Date(`${range[0]}T00:00:00Z`))} — {range.at(-1) && dateFormatter.format(new Date(`${range.at(-1)}T00:00:00Z`))}</strong></div>
        </div>
        <label className="history-slider"><span className="field-label">Глубина истории</span><input type="range" min="0" max={maxOffset} value={maxOffset - offset} onChange={(event) => setOffset(maxOffset - Number(event.target.value))} /></label>
        {grouped.length > 0 && (
          <button
            type="button"
            className="secondary-button groups-toggle"
            onClick={() => setExpanded(allOpen ? new Set() : new Set(grouped.map((group) => group.id)))}
          >
            {allOpen ? "Свернуть болванки" : "Показать болванки"}
          </button>
        )}
        <span className="five-star-key"><MessageSquare size={13} /> отзывы по артикулу{grouped.length > 0 && <> · <Sigma size={13} /> сумма по группе</>}</span>
      </div>
      {groupError && <div className="inline-error">{groupError}</div>}

      <div className="timeline-table">
        <div className="timeline-grid timeline-head">
          <div className="product-head">Товар и артикул</div>
          {range.map((date) => <div className={date === today ? "today-cell" : ""} key={date}><span>{dateFormatter.format(new Date(`${date}T00:00:00Z`))}</span><b>{date === today ? "Сегодня" : weekdayFormatter.format(new Date(`${date}T00:00:00Z`))}</b></div>)}
        </div>
        {groups.map((group) => (
          <div className={`product-group${isGroup(group) ? " product-group-multi" : ""}`} key={group.id}>
            {renderRow(group.main, group, false)}
            {isOpen(group) && group.members.map((member) => renderRow(member, group, true))}
          </div>
        ))}
      </div>
      {attaching && (
        <AttachToMainDialog
          product={attaching}
          targets={targets.filter((target) => target.id !== attaching.id)}
          onClose={() => setAttaching(null)}
          onSubmit={(mainArticle) => {
            onGroupAction?.(attaching.article, "attach", mainArticle);
            setAttaching(null);
          }}
        />
      )}
    </section>
  );
}
