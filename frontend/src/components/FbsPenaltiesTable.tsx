import { periodLabel, money } from "../features/fbsPenalties/period";
import type { PenaltyRow } from "../features/fbsPenalties/types";

const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });

const TEMPLATE = [
  "150px", // неделя отчёта
  "230px", // товар
  "230px", // вид удержания
  "110px", // сумма
  "125px", // стикер
  "150px", // сборочное задание
  "125px", // дата заказа
  "200px", // склад продавца
  "150px", // поставка
  "125px", // создана
  "125px", // QR
  "180px", // склад WB
].join(" ");

function stamp(value: string | null): string {
  return value ? momentFormatter.format(new Date(value)) : "—";
}

/** Таблица удержаний: те же столбцы, что в её Google-таблице, плюс то, что она заполняла руками. */
export function FbsPenaltiesTable({ rows, empty }: { rows: PenaltyRow[]; empty: string }) {
  return (
    <section className="checklist-scroll stocks-scroll" style={{ "--stocks-template": TEMPLATE } as React.CSSProperties}>
      <div className="stocks-head">
        <span className="checklist-sticky">Неделя отчёта</span>
        <span>Товар</span>
        <span>Вид удержания</span>
        <span>Сумма</span>
        <span>Стикер МП</span>
        <span>Сборочное задание · srid</span>
        <span>Дата заказа</span>
        <span>Склад продавца</span>
        <span>Поставка</span>
        <span>Поставка создана</span>
        <span>QR отсканирован</span>
        <span>Склад WB</span>
      </div>
      {rows.map((row) => (
        <div className="stocks-row" key={`${row.rrd_id}:${row.srid}:${row.sticker_id}`}>
          <span className="checklist-sticky penalty-period">
            {row.rrd_id ? periodLabel(row.report_period) : <em className="stocks-unknown">не в отчётах</em>}
          </span>
          <span className="stocks-product">
            <strong>{row.title || row.barcode || "—"}</strong>
            <em>
              {row.nm_id ? (
                <a
                  className="wb-article-link"
                  href={`https://www.wildberries.ru/catalog/${row.nm_id}/detail.aspx`}
                  target="_blank"
                  rel="noreferrer"
                >
                  {row.nm_id}
                </a>
              ) : null}
              {row.barcode && ` · ${row.barcode}`}
            </em>
          </span>
          <span className="penalty-kind">
            <strong>{row.kind || "—"}</strong>
            <em>{row.group_title}</em>
          </span>
          <span className={`stocks-cell ${row.amount ? "penalty-amount" : ""}`}>{row.amount ? money(row.amount) : "—"}</span>
          <span className="stocks-barcode">{row.sticker_id || "—"}</span>
          <span className="penalty-ids">
            <strong>{row.assembly_id || "—"}</strong>
            <em title={row.srid}>{row.srid}</em>
          </span>
          <span>{stamp(row.order_dt)}</span>
          <span className={row.trace === "no_order" ? "stocks-unknown" : undefined}>
            {row.trace === "no_order" ? "задание не найдено" : row.warehouse_name}
          </span>
          <span className={row.trace === "found" ? "stocks-barcode" : "stocks-unknown"}>
            {row.trace === "found" ? row.supply_id : row.trace === "no_supply" ? "без поставки" : "—"}
          </span>
          <span>{stamp(row.supply_created_at)}</span>
          <span>{stamp(row.supply_scan_dt)}</span>
          <span>{row.destination_office_name ?? "—"}</span>
        </div>
      ))}
      {rows.length === 0 && <span className="fbs-row-empty">{empty}</span>}
    </section>
  );
}
