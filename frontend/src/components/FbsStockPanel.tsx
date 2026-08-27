import { useQuery } from "@tanstack/react-query";
import { PlugZap } from "lucide-react";

import { getPools, getStockHistory, getStockState } from "../features/fbs/api";

const moment = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });

const sourceLabels: Record<string, string> = {
  "1c": "обмен с 1С",
  disconnected: "1С не подключена",
};

export function FbsStockPanel() {
  const { data: state } = useQuery({ queryKey: ["fbs-stock"], queryFn: getStockState });
  const { data: history = [] } = useQuery({ queryKey: ["fbs-stock-history"], queryFn: getStockHistory });
  const { data: pools = [] } = useQuery({ queryKey: ["fbs-pools", ""], queryFn: () => getPools("") });

  const connected = Boolean(state?.snapshot_id);
  const dash = (value: number | undefined) => (connected ? String(value ?? 0) : "—");

  return (
    <>
      <div className="reviews-heading">
        <div>
          <h2>Остаток 1С</h2>
        </div>
      </div>

      {!connected && (
        <section className="fbs-notice" aria-label="Состояние обмена с 1С">
          <span className="fbs-notice-mark" aria-hidden="true">
            <PlugZap size={24} />
          </span>
          <div>
            <strong>1С ещё не подключена</strong>
            <p>
              Обмена нет, поэтому распределение не считается. Когда обмен появится, остаток будет приходить сюда
              сам, и этот экран заполнится: сверху — свежесть снимка и сколько единиц доступно после резерва, ниже
              — позиции и журнал обменов.
            </p>
          </div>
        </section>
      )}

      <dl className="automation-meta">
        <div>
          <dt>Источник</dt>
          <dd>
            {sourceLabels[state?.source ?? "disconnected"] ?? state?.source}
            <span className="automation-run-detail">
              {state?.generated_at ? `сформирован ${moment.format(new Date(state.generated_at))}` : "—"}
            </span>
          </dd>
        </div>
        <div>
          <dt>Свежесть</dt>
          <dd>
            {connected ? (state?.stale ? "устарел" : "актуален") : "—"}
            <span className="automation-run-detail">
              {state?.received_at ? `принят ${moment.format(new Date(state.received_at))}` : "—"}
            </span>
          </dd>
        </div>
        <div>
          <dt>Физический остаток</dt>
          <dd>
            {dash(state?.on_hand_total)}
            <span className="automation-run-detail">{dash(state?.pools)} позиций</span>
          </dd>
        </div>
        <div>
          <dt>Доступно после резерва</dt>
          <dd>
            {dash(state?.available_total)}
            <span className="automation-run-detail">резерв {state?.reserve_units ?? 0} на товар</span>
          </dd>
        </div>
      </dl>

      <div className="reviews-heading">
        <div>
          <h2>Позиции</h2>
        </div>
      </div>
      <section className="fbs-pool-table" aria-label="Позиции 1С">
        <div className="fbs-pool-head">
          <span>Наименование</span>
          <span>Характеристика</span>
          <span>Баркод</span>
          <span>Остаток</span>
          <span>Доступно</span>
        </div>
        {pools.map((pool) => (
          <div className="fbs-pool-row" key={`${pool.item_id}/${pool.characteristic}`}>
            <span>
              {pool.name || "—"}
              <em className="stock-split">{pool.item_id}</em>
            </span>
            <span>{pool.characteristic || "—"}</span>
            <span>{pool.barcode || "—"}</span>
            <span>{pool.on_hand}</span>
            <span>{pool.available}</span>
          </div>
        ))}
        {!pools.length && <div className="fbs-pool-row fbs-row-empty">Появятся, когда 1С отдаст первый остаток.</div>}
      </section>

      <div className="reviews-heading">
        <div>
          <h2>Журнал обменов</h2>
        </div>
      </div>
      {history.length ? (
        <ol className="fbs-queue">
          {history.map((record) => (
            <li key={record.id}>
              <b>{record.status === "accepted" ? "принят" : "отклонён"}</b>{" "}
              {moment.format(new Date(record.received_at))}
              <em className="stock-split">
                {record.lines} строк · сформирован {moment.format(new Date(record.generated_at))}
                {record.error && ` · ${record.error}`}
              </em>
            </li>
          ))}
        </ol>
      ) : (
        <ol className="fbs-queue">
          <li className="fbs-row-empty">Обменов ещё не было.</li>
        </ol>
      )}
    </>
  );
}
