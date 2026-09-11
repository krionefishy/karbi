import { useQuery } from "@tanstack/react-query";
import type { CSSProperties } from "react";

import { getBoards } from "../features/fbsStocks/api";
import type { StocksBoard } from "../features/fbsStocks/types";

/**
 * Лист «Сравнение»: все кабинеты друг под другом. Свои склады раскрыты, округа
 * стоят суммами — чтобы увидеть, где товар лежит у нас и чего не хватает в регионе.
 */
export function FbsStocksComparison() {
  const { data: boards = [], isLoading } = useQuery({ queryKey: ["fbs-stocks-boards"], queryFn: getBoards });
  if (isLoading) return <div className="loading-block">Собираем кабинеты…</div>;
  if (boards.length === 0) return <div className="loading-block">Подключите кабинеты — сравнение соберётся из их таблиц.</div>;
  return (
    <>
      {boards.map((board) => (
        <ComparisonBlock key={board.seller_id} board={board} />
      ))}
    </>
  );
}

function ComparisonBlock({ board }: { board: StocksBoard }) {
  const own = board.groups.find((group) => group.kind === "own");
  const districts = board.groups.filter((group) => group.kind === "district");
  const ownColumns = own?.columns ?? [];
  const template = ["200px", "150px", "112px", ...ownColumns.map(() => "96px"), ...districts.map(() => "128px")].join(" ");
  return (
    <section className="card stocks-compare" aria-label={`Сравнение: ${board.seller_name}`}>
      <div className="card-head">
        <div className="card-head-title">
          <h2>{board.seller_name}</h2>
          <span className="muted">{board.rows.length} баркодов</span>
        </div>
      </div>
      <div className="checklist-scroll" style={{ "--stocks-template": template } as CSSProperties}>
        <div className="stocks-head">
          <span className="checklist-sticky">Заметка</span>
          <span>Баркод</span>
          <span className="stocks-group-head stocks-group-static">{own?.title ?? "Наш склад"}</span>
          {ownColumns.map((column) => (
            <span key={column.warehouse_id} className="stocks-column-head" title={column.name}>
              {column.name}
            </span>
          ))}
          {districts.map((group) => (
            <span key={group.id} className="stocks-group-head stocks-group-static">
              {group.title}
            </span>
          ))}
        </div>
        {board.rows.map((row) => (
          <div className="stocks-row" key={row.barcode}>
            <span className="checklist-sticky stocks-note-static">{row.note || "—"}</span>
            <span className="stocks-barcode">{row.barcode}</span>
            <Cell value={own ? (row.totals[own.id] ?? 0) : 0} group />
            {ownColumns.map((column) => (
              <Cell key={column.warehouse_id} value={row.amounts[String(column.warehouse_id)] ?? 0} />
            ))}
            {districts.map((group) => (
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
