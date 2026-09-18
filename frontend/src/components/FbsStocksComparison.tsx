import { useQuery } from "@tanstack/react-query";
import type { CSSProperties } from "react";

import { getBoards } from "../features/fbsStocks/api";
import type { GroupKind, StocksBoard } from "../features/fbsStocks/types";

interface Props {
  /** Какую группу раскрывать по складам: свои склады на «Сравнении», фулфилмент на «Сравнении ФФ». */
  expand: GroupKind;
}

const FALLBACK_TITLE: Record<GroupKind, string> = { own: "Наш склад", fulfilment: "Фулфилмент", district: "Округ" };

/**
 * Листы сравнения: все кабинеты друг под другом. Одна группа раскрыта по складам, остальные
 * стоят суммами — чтобы увидеть, где товар лежит и чего не хватает в регионе.
 */
export function FbsStocksComparison({ expand }: Props) {
  const { data: boards = [], isLoading } = useQuery({ queryKey: ["fbs-stocks-boards"], queryFn: getBoards });
  if (isLoading) return <div className="loading-block">Собираем кабинеты…</div>;
  if (boards.length === 0) return <div className="loading-block">Подключите кабинеты — сравнение соберётся из их таблиц.</div>;
  return (
    <>
      {boards.map((board) => (
        <ComparisonBlock key={board.seller_id} board={board} expand={expand} />
      ))}
    </>
  );
}

function ComparisonBlock({ board, expand }: { board: StocksBoard; expand: GroupKind }) {
  const own = board.groups.find((group) => group.kind === expand);
  const summaries = board.groups.filter((group) => group.kind !== expand);
  const ownColumns = own?.columns ?? [];
  const template = ["200px", "150px", "112px", ...ownColumns.map(() => "96px"), ...summaries.map(() => "128px")].join(" ");
  return (
    <section className="card stocks-compare" aria-label={`Сравнение: ${board.seller_name}`}>
      <div className="card-head">
        <div className="card-head-title">
          <h2>{board.seller_name}</h2>
          <span className="muted">{board.rows.length} баркодов</span>
        </div>
      </div>
      <div className="checklist-scroll stocks-scroll" style={{ "--stocks-template": template } as CSSProperties}>
        <div className="stocks-head">
          <span className="checklist-sticky">Заметка</span>
          <span className="stocks-sticky-barcode">Баркод</span>
          <span className="stocks-group-head stocks-group-static">{own?.title ?? FALLBACK_TITLE[expand]}</span>
          {ownColumns.map((column) => (
            <span key={column.warehouse_id} className="stocks-column-head" title={column.name}>
              {column.name}
            </span>
          ))}
          {summaries.map((group) => (
            <span key={group.id} className="stocks-group-head stocks-group-static">
              {group.title}
            </span>
          ))}
        </div>
        {board.rows.map((row) => (
          <div className="stocks-row" key={row.barcode}>
            <span className="checklist-sticky stocks-note-static">{row.note || "—"}</span>
            <span className="stocks-barcode stocks-sticky-barcode">{row.barcode}</span>
            <Cell value={own ? (row.totals[own.id] ?? 0) : 0} group />
            {ownColumns.map((column) => (
              <Cell key={column.warehouse_id} value={row.amounts[String(column.warehouse_id)] ?? 0} />
            ))}
            {summaries.map((group) => (
              <Cell key={group.id} value={row.totals[group.id] ?? 0} group />
            ))}
          </div>
        ))}
        {board.rows.length === 0 && <span className="fbs-row-empty">Баркоды ещё не вписаны.</span>}
      </div>
    </section>
  );
}

function Cell({ value, group = false }: { value: number; group?: boolean }) {
  return (
    <span className={`stocks-cell ${group ? "stocks-cell-group" : ""} ${value === 0 ? "stocks-zero" : ""}`}>{value}</span>
  );
}
