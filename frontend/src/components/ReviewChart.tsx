import { useMemo, useState } from "react";

import {
  buildSeries,
  CHART_DAYS,
  formatSigned,
  runsOf,
  summarize,
  ticksFor,
  verticalScale,
} from "../features/reviews/chart";
import type { ChartPoint } from "../features/reviews/chart";
import type { DailyReviewSnapshot } from "../features/reviews/types";

interface ReviewChartProps {
  snapshots: DailyReviewSnapshot[];
  cardSnapshots: DailyReviewSnapshot[];
  endDate: string;
}

type Scope = "article" | "card";

// One viewBox, scaled by CSS. The totals line lives above LINE_BOTTOM, the
// daily change hangs off ZERO_Y below it.
const VIEW = { width: 1000, height: 300 };
const LEFT = 52;
const RIGHT = 990;
const TOP = 18;
const LINE_BOTTOM = 168;
const ZERO_Y = 226;
const BAR_MAX = 44;

const dateFormatter = new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "short", timeZone: "UTC" });

function formatDate(date: string): string {
  return dateFormatter.format(new Date(`${date}T00:00:00Z`));
}

export function ReviewChart({ snapshots, cardSnapshots, endDate }: ReviewChartProps) {
  const [scope, setScope] = useState<Scope>("article");
  const [focused, setFocused] = useState<number | null>(null);

  const source = scope === "card" && cardSnapshots.length ? cardSnapshots : snapshots;
  const points = useMemo(() => buildSeries(source, endDate, CHART_DAYS), [source, endDate]);
  const scale = useMemo(() => verticalScale(points), [points]);
  const runs = useMemo(() => runsOf(points), [points]);
  const totals = useMemo(() => summarize(points), [points]);

  const step = (RIGHT - LEFT) / Math.max(points.length - 1, 1);
  const x = (index: number) => LEFT + index * step;
  const y = (value: number) =>
    LINE_BOTTOM - ((value - scale.floor) / (scale.ceiling - scale.floor)) * (LINE_BOTTOM - TOP);

  const biggest = Math.max(...points.map((point) => Math.abs(point.delta ?? 0)), 1);
  const barWidth = Math.max(4, step * 0.52);

  const linePath = runs
    .map((run) => run.map((point, at) => `${at ? "L" : "M"}${x(point.index).toFixed(1)} ${y(point.total).toFixed(1)}`).join(" "))
    .join(" ");
  const areaPath = runs
    .map((run) => {
      const head = run
        .map((point, at) => `${at ? "L" : "M"}${x(point.index).toFixed(1)} ${y(point.total).toFixed(1)}`)
        .join(" ");
      return `${head} L${x(run[run.length - 1].index).toFixed(1)} ${LINE_BOTTOM} L${x(run[0].index).toFixed(1)} ${LINE_BOTTOM} Z`;
    })
    .join(" ");
  // A dashed bridge over a gap: it connects the eye without claiming we know
  // what happened in between.
  const gapPath = runs
    .slice(0, -1)
    .map((run, at) => {
      const from = run[run.length - 1];
      const to = runs[at + 1][0];
      return `M${x(from.index).toFixed(1)} ${y(from.total).toFixed(1)} L${x(to.index).toFixed(1)} ${y(to.total).toFixed(1)}`;
    })
    .join(" ");

  const active: ChartPoint | null = focused === null ? null : (points[focused] ?? null);
  const hasHistory = totals.latestTotal !== null;

  return (
    <div className="review-chart">
      <div className="chart-toolbar">
        <div className="chart-range">
          <span className="detail-date">
            {formatDate(points[0].date)} — {formatDate(points[points.length - 1].date)}
          </span>
          <span className="muted">Динамика за {CHART_DAYS} дней</span>
        </div>
        {cardSnapshots.length > 0 && (
          <div className="chart-scope" role="group" aria-label="Что показывать на графике">
            {(["article", "card"] as const).map((option) => (
              <button
                type="button"
                key={option}
                className={scope === option ? "chart-scope-active" : ""}
                aria-pressed={scope === option}
                onClick={() => setScope(option)}
              >
                {option === "article" ? "По артикулу" : "По карточке"}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="chart-body">
        <div className="chart-plot">
          {hasHistory ? (
            <svg
              viewBox={`0 0 ${VIEW.width} ${VIEW.height}`}
              role="img"
              aria-label={`Изменение количества отзывов за ${CHART_DAYS} дней`}
              onMouseLeave={() => setFocused(null)}
            >
              {ticksFor(scale).map((value) => (
                <g key={value}>
                  <line x1={LEFT} y1={y(value)} x2={RIGHT} y2={y(value)} className="chart-grid" />
                  <text x={LEFT - 8} y={y(value) + 3} textAnchor="end" className="chart-axis">
                    {value.toLocaleString("ru-RU")}
                  </text>
                </g>
              ))}

              <path d={areaPath} className="chart-area" />
              <path d={linePath} className="chart-line" />
              {gapPath && <path d={gapPath} className="chart-bridge" />}

              {points.map((point, index) =>
                point.total !== null && point.delta ? (
                  <circle
                    key={point.date}
                    cx={x(index)}
                    cy={y(point.total)}
                    r={2.6}
                    className={point.delta > 0 ? "chart-dot-up" : "chart-dot-down"}
                  />
                ) : null,
              )}

              <line x1={LEFT} y1={ZERO_Y} x2={RIGHT} y2={ZERO_Y} className="chart-zero" />
              <text x={LEFT - 8} y={ZERO_Y + 3} textAnchor="end" className="chart-axis">
                0
              </text>

              {points.map((point, index) => {
                if (point.total === null) {
                  return (
                    <rect
                      key={point.date}
                      x={x(index) - barWidth / 2}
                      y={TOP - 4}
                      width={barWidth}
                      height={ZERO_Y + BAR_MAX - TOP + 4}
                      className="chart-blank"
                    />
                  );
                }
                if (!point.delta) return null;
                const height = Math.max(2, (Math.abs(point.delta) / biggest) * BAR_MAX);
                return (
                  <rect
                    key={point.date}
                    x={x(index) - barWidth / 2}
                    y={point.delta > 0 ? ZERO_Y - height : ZERO_Y}
                    width={barWidth}
                    height={height}
                    className={point.delta > 0 ? "chart-bar-up" : "chart-bar-down"}
                  />
                );
              })}

              {points.map((point, index) =>
                index % 6 === 0 || index === points.length - 1 ? (
                  <text key={point.date} x={x(index)} y={292} textAnchor="middle" className="chart-axis">
                    {formatDate(point.date)}
                  </text>
                ) : null,
              )}

              {focused !== null && (
                <line x1={x(focused)} y1={TOP - 4} x2={x(focused)} y2={ZERO_Y + BAR_MAX} className="chart-cursor" />
              )}

              {points.map((point, index) => (
                <rect
                  key={point.date}
                  x={x(index) - step / 2}
                  y={8}
                  width={step}
                  height={ZERO_Y + BAR_MAX - 8}
                  className="chart-hotspot"
                  onMouseEnter={() => setFocused(index)}
                />
              ))}
            </svg>
          ) : (
            <p className="chart-empty">За последние {CHART_DAYS} дней снимков по этому товару нет.</p>
          )}

          {active && (
            <div
              className="chart-tip"
              style={{ left: `${Math.min(Math.max((x(focused ?? 0) / VIEW.width) * 100, 6), 74)}%` }}
            >
              <span className="chart-tip-date">{formatDate(active.date)}</span>
              {active.total === null ? (
                <>
                  <b>Нет данных</b>
                  <span className="chart-tip-warn">синхронизация не отработала</span>
                </>
              ) : (
                <>
                  <b>{active.total.toLocaleString("ru-RU")}</b>
                  <span className={active.delta && active.delta < 0 ? "chart-tip-down" : "chart-tip-up"}>
                    {active.delta === null ? "изменение неизвестно" : `за день ${formatSigned(active.delta)}`}
                  </span>
                </>
              )}
            </div>
          )}
        </div>

        <dl className="chart-summary">
          <div>
            <dt>Всего сейчас</dt>
            <dd>{totals.latestTotal === null ? "—" : totals.latestTotal.toLocaleString("ru-RU")}</dd>
          </div>
          <div>
            <dt>За {CHART_DAYS} дней</dt>
            <dd className={totals.change && totals.change < 0 ? "chart-value-down" : "chart-value-up"}>
              {totals.change === null ? "—" : formatSigned(totals.change)}
            </dd>
          </div>
          <div>
            <dt>Дней с движением</dt>
            <dd>
              {totals.movedDays} из {points.length}
            </dd>
          </div>
          <div>
            <dt>Дней без данных</dt>
            <dd className={totals.missingDays ? "chart-value-warn" : ""}>{totals.missingDays}</dd>
          </div>
          <p className="chart-note">
            Шкала слева обрезана по диапазону данных — иначе прирост в несколько отзывов на фоне тысячи не разглядеть.
          </p>
        </dl>
      </div>
    </div>
  );
}
