import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload } from "lucide-react";
import { useRef, useState } from "react";

import { ApiError } from "../api/http";
import { getPools, getStockHistory, getStockState, uploadStock } from "../features/fbs/api";

const moment = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });

const sourceLabels: Record<string, string> = {
  manual: "загружен вручную",
  disconnected: "обмена с 1С нет",
};

export function FbsStockPanel() {
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [search, setSearch] = useState("");
  const [error, setError] = useState("");

  const { data: state } = useQuery({ queryKey: ["fbs-stock"], queryFn: getStockState });
  const { data: history = [] } = useQuery({ queryKey: ["fbs-stock-history"], queryFn: getStockHistory });
  const { data: pools = [] } = useQuery({ queryKey: ["fbs-pools", search], queryFn: () => getPools(search) });

  const upload = useMutation({
    mutationFn: uploadStock,
    onSuccess: async () => {
      setError("");
      await queryClient.invalidateQueries({ queryKey: ["fbs-stock"] });
      await queryClient.invalidateQueries({ queryKey: ["fbs-stock-history"] });
      await queryClient.invalidateQueries({ queryKey: ["fbs-pools"] });
    },
    onError: (raised) => setError(raised instanceof ApiError ? raised.message : "Снимок не принят"),
  });

  async function choose(file: File | undefined) {
    if (!file) return;
    upload.mutate(await file.text());
    if (fileInput.current) fileInput.current.value = "";
  }

  return (
    <>
      <div className="reviews-heading">
        <div>
          <h2>Остаток 1С</h2>
          <p className="muted">
            Обмена с 1С пока нет: снимок загружается файлом. Принимается абсолютный остаток целиком — CSV или
            JSON. Повторная загрузка того же файла ничего не задваивает.
          </p>
        </div>
        <button className="primary-button" disabled={upload.isPending} onClick={() => fileInput.current?.click()}>
          <Upload size={16} />
          {upload.isPending ? "Принимаем…" : "Загрузить снимок"}
        </button>
        <input
          ref={fileInput}
          type="file"
          accept=".csv,.json,.txt,text/csv,application/json"
          hidden
          aria-label="Файл выгрузки 1С"
          onChange={(event) => void choose(event.target.files?.[0])}
        />
      </div>
      {error && <div className="form-error">{error}</div>}

      <section className="automation-meta" aria-label="Состояние остатка 1С">
        <div>
          <dt>Источник</dt>
          <dd>
            {sourceLabels[state?.source ?? "disconnected"] ?? state?.source}
            <span className="automation-run-detail">
              {state?.generated_at
                ? `сформирован ${moment.format(new Date(state.generated_at))}`
                : "снимок ещё не принимался"}
            </span>
          </dd>
        </div>
        <div>
          <dt>Свежесть</dt>
          <dd>
            {state?.stale ? "устарел" : "актуален"}
            <span className="automation-run-detail">
              {state?.received_at ? `принят ${moment.format(new Date(state.received_at))}` : "—"}
            </span>
          </dd>
        </div>
        <div>
          <dt>Физический остаток</dt>
          <dd>
            {state?.on_hand_total ?? 0}
            <span className="automation-run-detail">{state?.pools ?? 0} позиций</span>
          </dd>
        </div>
        <div>
          <dt>Доступно после резерва</dt>
          <dd>
            {state?.available_total ?? 0}
            <span className="automation-run-detail">резерв {state?.reserve_units ?? 0} на товар</span>
          </dd>
        </div>
      </section>

      <div className="reviews-heading">
        <div>
          <h2>Позиции</h2>
          <p className="muted">Остаток каждой номенклатуры и сколько из него уйдёт на склады.</p>
        </div>
      </div>
      <div className="fbs-office-tools">
        <input
          aria-label="Поиск позиции"
          placeholder="Наименование, баркод или код 1С"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
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
        {!pools.length && <div className="fbs-pool-row">Позиций нет — загрузите снимок.</div>}
      </section>

      {history.length > 0 && (
        <>
          <div className="reviews-heading">
            <div>
              <h2>Журнал выгрузок</h2>
              <p className="muted">Отклонённые тоже здесь: битый файл — это событие, а не тишина.</p>
            </div>
          </div>
          <ol className="fbs-queue">
            {history.map((record) => (
              <li key={record.id}>
                <b>{record.status === "accepted" ? "принят" : "отклонён"}</b> {moment.format(new Date(record.received_at))}
                <em className="stock-split">
                  {record.lines} строк · сформирован {moment.format(new Date(record.generated_at))}
                  {record.error && ` · ${record.error}`}
                </em>
              </li>
            ))}
          </ol>
        </>
      )}
    </>
  );
}
