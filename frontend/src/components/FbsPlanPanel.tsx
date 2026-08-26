import { useMutation } from "@tanstack/react-query";
import { Calculator } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api/http";
import { buildPlan } from "../features/fbs/api";
import type { FbsPlan } from "../features/fbs/types";

interface Props {
  sellerId: string;
  sellerName: string;
}

const moment = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });

export function FbsPlanPanel({ sellerId, sellerName }: Props) {
  const [plan, setPlan] = useState<FbsPlan | null>(null);
  const [error, setError] = useState("");
  const [opened, setOpened] = useState<number | null>(null);

  const build = useMutation({
    mutationFn: () => buildPlan(sellerId),
    onSuccess: (result) => {
      setError("");
      setPlan(result);
    },
    onError: (raised) => setError(raised instanceof ApiError ? raised.message : "Не удалось посчитать план"),
  });

  return (
    <>
      <div className="reviews-heading">
        <div>
          <h2>Расчёт распределения</h2>
          <p className="muted">
            Считает план по «{sellerName}» и показывает его. В Wildberries ничего не отправляется.
          </p>
        </div>
        <button className="primary-button" disabled={build.isPending} onClick={() => build.mutate()}>
          <Calculator size={16} />
          {build.isPending ? "Считаем…" : "Посчитать"}
        </button>
      </div>
      {error && <div className="form-error">{error}</div>}

      {plan && (
        <>
          <section className="automation-meta" aria-label="Итоги расчёта">
            <div>
              <dt>Посчитано</dt>
              <dd>
                {moment.format(new Date(plan.created_at))}
                <span className="automation-run-detail">
                  резерв {plan.reserve_units} · приоритетных направлений {plan.priority_regions}
                </span>
              </dd>
            </div>
            <div>
              <dt>Разложено единиц</dt>
              <dd>
                {plan.units}
                <span className="automation-run-detail">по {plan.warehouses} складам</span>
              </dd>
            </div>
            <div>
              <dt>Позиций в плане</dt>
              <dd>{plan.items.length}</dd>
            </div>
            <div>
              <dt>Не попало в план</dt>
              <dd>{plan.skips.length}</dd>
            </div>
          </section>

          <section className="fbs-pool-table" aria-label="План по позициям">
            <div className="fbs-pool-head">
              <span>Позиция</span>
              <span>Баркод</span>
              <span>Остаток</span>
              <span>Доступно</span>
              <span>Разложено</span>
            </div>
            {plan.items.map((item) => (
              <div key={item.chrt_id}>
                <div
                  className="fbs-pool-row fbs-plan-row"
                  role="button"
                  tabIndex={0}
                  onClick={() => setOpened(opened === item.chrt_id ? null : item.chrt_id)}
                  onKeyDown={(event) => event.key === "Enter" && setOpened(opened === item.chrt_id ? null : item.chrt_id)}
                >
                  <span>
                    {item.name || "—"}
                    <em className="stock-split">
                      {item.item_id}
                      {item.characteristic && ` · ${item.characteristic}`} · размер {item.chrt_id}
                    </em>
                  </span>
                  <span>{item.barcode || "—"}</span>
                  <span>{item.on_hand}</span>
                  <span>{item.available}</span>
                  <span>{item.units}</span>
                </div>
                {opened === item.chrt_id && (
                  <ol className="fbs-queue">
                    {item.amounts.map((amount) => (
                      <li key={amount.warehouse_id}>
                        <b>{amount.amount}</b> {amount.name || amount.warehouse_id}
                        <em className="stock-split">
                          {amount.region_code ?? "без направления"}
                          {amount.city && ` · ${amount.city}`}
                        </em>
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            ))}
            {!plan.items.length && <div className="fbs-pool-row">Ни одна позиция не попала в план.</div>}
          </section>

          {plan.skips.length > 0 && (
            <>
              <div className="reviews-heading">
                <div>
                  <h2>Не попало в план</h2>
                  <p className="muted">Пропуск без причины неотличим от «товара нет» — здесь причина у каждого.</p>
                </div>
              </div>
              <ol className="fbs-queue">
                {plan.skips.map((skip) => (
                  <li key={skip.chrt_id}>
                    <b>{skip.chrt_id}</b> {skip.name || skip.item_id}
                    <em className="stock-split">{skip.text}</em>
                  </li>
                ))}
              </ol>
            </>
          )}
        </>
      )}
    </>
  );
}
