import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import {
  dailySeries,
  dailyTip,
  percent,
  rateTip,
  replyLabel,
  sortedCategories,
  tipText,
  type DayTip,
  type TipSwatch,
} from "../features/supportBot/format";
import type {
  DailyPoint,
  ReplyBucket,
  ReplyCategoryCount,
} from "../features/supportBot/types";

const dateFormatter = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  timeZone: "UTC",
});
const shortDate = (date: string) =>
  dateFormatter.format(new Date(`${date}T00:00:00Z`));

/** Дата live_since по Москве в виде YYYY-MM-DD. */
export function moscowDate(value: string): string {
  return new Date(value).toLocaleDateString("sv-SE", {
    timeZone: "Europe/Moscow",
  });
}

export const BUCKET_COLOR: Record<ReplyBucket, string> = {
  threat: "var(--negative)",
  stop_request: "var(--warning-mark)",
  thanks: "var(--chart-2)",
  cant_delete: "var(--chart-3)",
  refuses: "var(--positive)",
  not_helped: "color-mix(in srgb, var(--positive) 70%, var(--surface))",
  noise: "color-mix(in srgb, var(--positive) 45%, var(--surface))",
  waiting_seller: "var(--chart-1)",
  sent_details: "color-mix(in srgb, var(--chart-1) 55%, var(--surface))",
  question: "color-mix(in srgb, var(--chart-3) 55%, var(--surface))",
  says_deleted: "color-mix(in srgb, var(--chart-2) 55%, var(--surface))",
  unclear: "var(--ink-muted)",
  stopword: "var(--line-strong)",
  attachment: "var(--ink-disabled)",
  undetermined: "var(--line)",
};

export const ACTION_COLOR = {
  continue: "var(--positive)",
  stop: "var(--negative)",
  wait: "var(--line-strong)",
} as const;

export interface DonutSlice {
  key: string;
  label: string;
  count: number;
  color: string;
}

export function Donut({
  slices,
  label,
  size = 160,
}: {
  slices: DonutSlice[];
  label: string;
  size?: number;
}) {
  const total = slices.reduce((sum, slice) => sum + slice.count, 0);
  let offset = 0;
  return (
    <div className="rp-donut-block">
      <svg
        viewBox="0 0 36 36"
        width={size}
        height={size}
        role="img"
        aria-label={label}
      >
        <circle
          cx="18"
          cy="18"
          r="15.9155"
          fill="none"
          stroke="var(--surface-hover)"
          strokeWidth="5"
        />
        {total > 0 &&
          slices.map((slice) => {
            const share = (slice.count / total) * 100;
            const start = offset;
            offset += share;
            return (
              <circle
                key={slice.key}
                cx="18"
                cy="18"
                r="15.9155"
                fill="none"
                strokeWidth="5"
                style={{ stroke: slice.color }}
                strokeDasharray={`${share} ${100 - share}`}
                strokeDashoffset={-start}
                transform="rotate(-90 18 18)"
              >
                <title>{`${slice.label}: ${slice.count}`}</title>
              </circle>
            );
          })}
        <text x="18" y="19.2" textAnchor="middle" className="rp-donut-total">
          {total}
        </text>
      </svg>
      <ul className="rp-legend">
        {slices.map((slice) => (
          <li key={slice.key}>
            <i style={{ background: slice.color }} aria-hidden="true" />
            <span>{slice.label}</span>
            <strong>{slice.count}</strong>
            <em>{percent(slice.count, total)}</em>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function CategoryDonut({
  items,
  label,
}: {
  items: ReplyCategoryCount[];
  label: string;
}) {
  const slices = sortedCategories(items).map((item) => ({
    key: item.category,
    label: replyLabel(item.category),
    count: item.count,
    color: BUCKET_COLOR[item.category] ?? "var(--line-strong)",
  }));
  return <Donut slices={slices} label={label} />;
}

const SWATCH_COLOR: Record<TipSwatch, string> = {
  live: "var(--accent)",
  control: "var(--line-strong)",
  total: "var(--ink-secondary)",
};

/**
 * Состояние подсказки по дню: наведение и фокус показывают её, нажатие закрепляет,
 * повторное нажатие, Esc или касание вне дней убирают.
 */
function useDayTip() {
  const [active, setActive] = useState<number | null>(null);
  const [pinned, setPinned] = useState<number | null>(null);

  useEffect(() => {
    if (pinned === null) return undefined;
    const outside = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (target?.closest?.("[data-tip-hit]")) return;
      setPinned(null);
      setActive(null);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [pinned]);

  const toggle = (at: number) => {
    if (pinned === at) {
      setPinned(null);
      setActive(null);
    } else {
      setPinned(at);
      setActive(at);
    }
  };

  const hit = (at: number, tip: DayTip) => {
    const show = () => {
      if (pinned === null) setActive(at);
    };
    const hide = () => {
      if (pinned === null) setActive(null);
    };
    return {
      "data-tip-hit": "",
      tabIndex: 0,
      role: "button" as const,
      "aria-label": tipText(tip),
      "aria-describedby": active === at ? "rp-tooltip" : undefined,
      onMouseEnter: show,
      onMouseLeave: hide,
      onFocus: show,
      onBlur: hide,
      onClick: () => toggle(at),
      onKeyDown: (event: KeyboardEvent) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          toggle(at);
        } else if (event.key === "Escape") {
          setPinned(null);
          setActive(null);
        }
      },
    };
  };

  return { active, hit };
}

/** Плавающая подсказка над графиком: держится внутри рамки и не перехватывает мышь. */
function Tooltip({ tip, anchor }: { tip: DayTip; anchor: number }) {
  const box = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = box.current;
    const parent = el?.parentElement;
    if (!el || !parent) return;
    const wrap = parent.clientWidth;
    const width = el.offsetWidth;
    const at = anchor * wrap;
    const gap = 12;
    const left = at + gap + width <= wrap ? at + gap : at - gap - width;
    el.style.left = `${Math.max(0, Math.min(left, wrap - width))}px`;
  });
  return (
    <div ref={box} id="rp-tooltip" role="tooltip" className="rp-tip">
      <strong>{tip.title}</strong>
      <dl>
        {tip.rows.map((row) => (
          <div key={row.label}>
            <dt>
              {row.swatch && (
                <i
                  style={{ background: SWATCH_COLOR[row.swatch] }}
                  aria-hidden="true"
                />
              )}
              {row.label}
            </dt>
            <dd>
              {row.value}
              {row.hint && <small>{row.hint}</small>}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function ChartWrap({
  tip,
  anchor,
  children,
}: {
  tip: DayTip | null;
  anchor: number;
  children: ReactNode;
}) {
  return (
    <div className="rp-chart-wrap">
      {children}
      {tip && <Tooltip tip={tip} anchor={anchor} />}
    </div>
  );
}

const W = 1000;
const LEFT = 44;
const RIGHT = 960;

function niceMax(value: number): number {
  if (value <= 4) return 4;
  const pow = 10 ** Math.floor(Math.log10(value));
  const step =
    [1, 2, 5, 10].find((candidate) => candidate * pow >= value) ?? 10;
  return step * pow;
}

function marker(dates: string[], liveSince: string | null): number | null {
  if (!liveSince) return null;
  const day = moscowDate(liveSince);
  const at = dates.findIndex((date) => date >= day);
  return at === -1 ? null : at;
}

const RATE_WINDOW_MS = 48 * 3600 * 1000;

/** Первый день, у которого окно 48 ч ещё не закрыто; null, если время сборки неизвестно. */
function matureBefore(generatedAt: string | undefined): string | null {
  if (!generatedAt) return null;
  return moscowDate(
    new Date(new Date(generatedAt).getTime() - RATE_WINDOW_MS).toISOString(),
  );
}

function labelEvery(count: number): number {
  return Math.max(1, Math.ceil(count / 12));
}

export function DailyBars({
  daily,
  liveSince,
  generatedAt,
}: {
  daily: DailyPoint[];
  liveSince: string | null;
  generatedAt?: string;
}) {
  const series = dailySeries(daily);
  const count = daily.length;
  const top = 16;
  const bottom = 190;
  const height = 230;
  const slot = (RIGHT - LEFT) / Math.max(count, 1);
  const barWidth = Math.max(3, slot * 0.62);
  const max = niceMax(
    Math.max(...series.live.map((value, at) => value + series.control[at]), 0),
  );
  const cumMax = niceMax(Math.max(...series.cumulative, 0));
  const y = (value: number) => bottom - (value / max) * (bottom - top);
  const yc = (value: number) => bottom - (value / cumMax) * (bottom - top);
  const cx = (at: number) => LEFT + slot * (at + 0.5);
  const every = labelEvery(count);
  const mark = marker(series.dates, liveSince);
  const { active, hit } = useDayTip();
  const matureUntil = matureBefore(generatedAt);
  const tips = daily.map((day, at) =>
    dailyTip(day, series.cumulative[at], {
      mature: matureUntil === null || day.date < matureUntil,
      marker: at === mark,
    }),
  );
  const line = series.cumulative
    .map(
      (value, at) =>
        `${at ? "L" : "M"}${cx(at).toFixed(1)} ${yc(value).toFixed(1)}`,
    )
    .join(" ");

  return (
    <ChartWrap
      tip={active === null ? null : tips[active]}
      anchor={active === null ? 0 : cx(active) / W}
    >
    <svg
      viewBox={`0 0 ${W} ${height}`}
      className="rp-chart"
      role="group"
      aria-label="Ответы покупателей по дням"
    >
      {active !== null && (
        <rect
          x={cx(active) - slot / 2}
          y={top - 6}
          width={slot}
          height={bottom - top + 6}
          fill="var(--surface-hover)"
        />
      )}
      {[0, 0.5, 1].map((part) => (
        <g key={part}>
          <line
            x1={LEFT}
            x2={RIGHT}
            y1={y(max * part)}
            y2={y(max * part)}
            stroke="var(--chart-grid)"
          />
          <text
            x={LEFT - 6}
            y={y(max * part) + 4}
            textAnchor="end"
            className="rp-axis"
          >
            {Math.round(max * part)}
          </text>
          <text x={RIGHT + 6} y={yc(cumMax * part) + 4} className="rp-axis">
            {Math.round(cumMax * part)}
          </text>
        </g>
      ))}
      {daily.map((day, at) => {
        const control = series.control[at];
        const live = series.live[at];
        return (
          <g key={day.date}>
            <rect
              x={cx(at) - barWidth / 2}
              y={y(control)}
              width={barWidth}
              height={bottom - y(control)}
              fill="var(--line-strong)"
            />
            <rect
              x={cx(at) - barWidth / 2}
              y={y(control + live)}
              width={barWidth}
              height={y(control) - y(control + live)}
              fill="var(--accent)"
            />
            {at % every === 0 && (
              <text
                x={cx(at)}
                y={bottom + 16}
                textAnchor="middle"
                className="rp-axis"
              >
                {shortDate(day.date)}
              </text>
            )}
          </g>
        );
      })}
      {count > 0 && (
        <path
          d={line}
          fill="none"
          stroke="var(--ink-secondary)"
          strokeWidth="2"
          strokeDasharray="6 4"
        />
      )}
      {mark !== null && (
        <g>
          <line
            x1={cx(mark) - slot / 2}
            x2={cx(mark) - slot / 2}
            y1={top - 6}
            y2={bottom}
            stroke="var(--warning-mark)"
            strokeWidth="2"
          />
          <text x={cx(mark) - slot / 2 + 4} y={top + 4} className="rp-axis">
            Режим 100%
          </text>
        </g>
      )}
      {daily.map((day, at) => (
        <rect
          key={day.date}
          className="rp-hit"
          x={cx(at) - slot / 2}
          y={0}
          width={slot}
          height={height}
          fill="transparent"
          {...hit(at, tips[at])}
        />
      ))}
    </svg>
    </ChartWrap>
  );
}

export function RateLines({
  daily,
  liveSince,
  generatedAt,
}: {
  daily: DailyPoint[];
  liveSince: string | null;
  generatedAt?: string;
}) {
  const base = dailySeries(daily);
  // Окно 48 ч у свежих дней ещё не закрыто: бэкенд не считает по ним ответы, и доля выглядела бы провалом до 0%.
  const matureUntil = matureBefore(generatedAt);
  const isMature = (at: number) =>
    matureUntil === null || base.dates[at] < matureUntil;
  const mask = (rates: (number | null)[]) =>
    rates.map((rate, at) => (isMature(at) ? rate : null));
  const series = {
    ...base,
    rate48Live: mask(base.rate48Live),
    rate48Control: mask(base.rate48Control),
  };
  const count = daily.length;
  const top = 12;
  const bottom = 130;
  const slot = (RIGHT - LEFT) / Math.max(count, 1);
  const cx = (at: number) => LEFT + slot * (at + 0.5);
  const values = [...series.rate48Live, ...series.rate48Control].filter(
    (value): value is number => value !== null,
  );
  const max = Math.max(0.2, Math.ceil(Math.max(...values, 0) * 10) / 10);
  const y = (value: number) => bottom - (value / max) * (bottom - top);
  const every = labelEvery(count);
  const mark = marker(series.dates, liveSince);
  const { active, hit } = useDayTip();
  const tips = daily.map((day, at) =>
    rateTip(day, { mature: isMature(at), marker: at === mark }),
  );

  const path = (rates: (number | null)[]) =>
    rates
      .map((rate, at) => ({ rate, at }))
      .filter(
        (item): item is { rate: number; at: number } => item.rate !== null,
      )
      .map(
        (item, index) =>
          `${index ? "L" : "M"}${cx(item.at).toFixed(1)} ${y(item.rate).toFixed(1)}`,
      )
      .join(" ");

  const dots = (rates: (number | null)[], color: string, name: string) =>
    rates.map((rate, at) =>
      rate === null ? null : (
        <circle
          key={`${name}${at}`}
          cx={cx(at)}
          cy={y(rate)}
          r="3"
          style={{ fill: color }} />
      ),
    );

  return (
    <ChartWrap
      tip={active === null ? null : tips[active]}
      anchor={active === null ? 0 : cx(active) / W}
    >
    <svg
      viewBox={`0 0 ${W} 160`}
      className="rp-chart"
      role="group"
      aria-label="Доля ответивших за 48 часов по дню старта"
    >
      {active !== null && (
        <rect
          x={cx(active) - slot / 2}
          y={top - 6}
          width={slot}
          height={bottom - top + 6}
          fill="var(--surface-hover)"
        />
      )}
      {[0, 0.5, 1].map((part) => (
        <g key={part}>
          <line
            x1={LEFT}
            x2={RIGHT}
            y1={y(max * part)}
            y2={y(max * part)}
            stroke="var(--chart-grid)"
          />
          <text
            x={LEFT - 6}
            y={y(max * part) + 4}
            textAnchor="end"
            className="rp-axis"
          >
            {Math.round(max * part * 100)}%
          </text>
        </g>
      ))}
      {daily.map(
        (day, at) =>
          at % every === 0 && (
            <text
              key={day.date}
              x={cx(at)}
              y={bottom + 16}
              textAnchor="middle"
              className="rp-axis"
            >
              {shortDate(day.date)}
            </text>
          ),
      )}
      <path
        d={path(series.rate48Control)}
        fill="none"
        stroke="var(--line-strong)"
        strokeWidth="2"
      />
      <path
        d={path(series.rate48Live)}
        fill="none"
        stroke="var(--accent)"
        strokeWidth="2"
      />
      {dots(series.rate48Control, "var(--line-strong)", "контроль")}
      {dots(series.rate48Live, "var(--accent)", "с напоминанием")}
      {mark !== null && (
        <line
          x1={cx(mark) - slot / 2}
          x2={cx(mark) - slot / 2}
          y1={top - 6}
          y2={bottom}
          stroke="var(--warning-mark)"
          strokeWidth="2"
        />
      )}
      {daily.map((day, at) => (
        <rect
          key={day.date}
          className="rp-hit"
          x={cx(at) - slot / 2}
          y={0}
          width={slot}
          height={160}
          fill="transparent"
          {...hit(at, tips[at])}
        />
      ))}
    </svg>
    </ChartWrap>
  );
}
