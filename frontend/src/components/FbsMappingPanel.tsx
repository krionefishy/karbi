import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2 } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api/http";
import { getMapping, rematchSeller, setPoolShares } from "../features/fbs/api";
import { toPercent } from "../features/fbs/shares";
import type { Seller } from "../features/sellers/types";

interface Props {
  sellers: Seller[];
}

export function FbsMappingPanel({ sellers }: Props) {
  const queryClient = useQueryClient();
  const { data: state } = useQuery({ queryKey: ["fbs-mapping"], queryFn: getMapping });
  const [error, setError] = useState("");
  const [note, setNote] = useState("");

  const names = new Map(sellers.map((seller) => [seller.id, seller.name]));
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["fbs-mapping"] });
  const fail = (fallback: string) => (raised: unknown) =>
    setError(raised instanceof ApiError ? raised.message : fallback);

  const rematch = useMutation({
    mutationFn: rematchSeller,
    onSuccess: async (result, sellerId) => {
      setError("");
      setNote(
        `«${names.get(sellerId) ?? sellerId}»: сопоставлено ${result.matched} из ${result.pools} позиций,` +
          ` в каталоге ${result.catalog_sizes} размеров.`,
      );
      await refresh();
    },
    onError: fail("Не удалось пересобрать связи"),
  });
  const shares = useMutation({
    mutationFn: setPoolShares,
    onSuccess: async () => {
      setError("");
      await refresh();
    },
    onError: fail("Не удалось сохранить деление пула"),
  });

  function splitEvenly(itemId: string, characteristic: string, owners: string[]) {
    // Ровное деление предлагается кнопкой, но не подставляется само: одинаковый
    // баркод в двух кабинетах ещё не значит, что запас между ними пополам.
    const base = Math.floor(10_000 / owners.length);
    const payload = Object.fromEntries(owners.map((id, index) => [id, index === 0 ? 10_000 - base * (owners.length - 1) : base]));
    shares.mutate({ item_id: itemId, characteristic, shares: payload });
  }

  return (
    <>
      <div className="reviews-heading">
        <div>
          <h2>Сопоставление с карточками</h2>
          <p className="muted">
            Позиция 1С находит размер карточки по баркоду. Сопоставлено {state?.mapped_pools ?? 0} из{" "}
            {state?.pools ?? 0}. Несопоставленные на Wildberries не выгружаются.
          </p>
        </div>
      </div>
      {error && <div className="form-error">{error}</div>}
      {note && <p className="muted">{note}</p>}

      <div className="fbs-office-tools">
        <span className="mode-switch">
          {sellers.map((seller) => (
            <button key={seller.id} disabled={rematch.isPending} onClick={() => rematch.mutate(seller.id)}>
              <Link2 size={14} /> {seller.name}
            </button>
          ))}
        </span>
      </div>

      {state && state.shared.length > 0 && (
        <>
          <div className="reviews-heading">
            <div>
              <h2>Общий запас нескольких кабинетов</h2>
              <p className="muted">
                Один баркод заведён в нескольких кабинетах — значит запас у них физически один. Без правила
                деления такой пул в расчёт не идёт: иначе каждый кабинет пообещает Wildberries весь остаток.
                Без правила осталось {state.shared_without_rule}.
              </p>
            </div>
          </div>
          <section className="fbs-pool-table" aria-label="Общие пулы">
            <div className="fbs-pool-head">
              <span>Позиция</span>
              <span>Баркод</span>
              <span>Остаток</span>
              <span>Кабинеты</span>
              <span>Деление</span>
            </div>
            {state.shared.map((pool) => (
              <div className="fbs-pool-row" key={`${pool.item_id}/${pool.characteristic}`}>
                <span>
                  {pool.name || "—"}
                  <em className="stock-split">{pool.item_id}</em>
                </span>
                <span>{pool.barcode || "—"}</span>
                <span>{pool.on_hand}</span>
                <span>
                  {pool.sellers.map((id) => names.get(id) ?? id).join(", ")}
                  {!pool.rule_ready && <em className="stock-split">правило не задано</em>}
                </span>
                <span>
                  {pool.rule_ready ? (
                    pool.sellers
                      .map((id) => `${names.get(id) ?? id}: ${toPercent(pool.shares[id] ?? 0)}%`)
                      .join(" · ")
                  ) : (
                    <button
                      className="primary-button"
                      disabled={shares.isPending}
                      onClick={() => splitEvenly(pool.item_id, pool.characteristic, pool.sellers)}
                    >
                      Разделить поровну
                    </button>
                  )}
                </span>
              </div>
            ))}
          </section>
        </>
      )}

      <div className="reviews-heading">
        <div>
          <h2>Без карточки</h2>
          <p className="muted">
            {state?.unmapped.length
              ? "Этим позициям не нашлось размера ни в одном подключённом кабинете."
              : "Все позиции сопоставлены."}
          </p>
        </div>
      </div>
      {state && state.unmapped.length > 0 && (
        <section className="fbs-pool-table" aria-label="Несопоставленные позиции">
          <div className="fbs-pool-head">
            <span>Наименование</span>
            <span>Характеристика</span>
            <span>Баркод</span>
            <span>Остаток</span>
            <span />
          </div>
          {state.unmapped.map((pool) => (
            <div className="fbs-pool-row" key={`${pool.item_id}/${pool.characteristic}`}>
              <span>
                {pool.name || "—"}
                <em className="stock-split">{pool.item_id}</em>
              </span>
              <span>{pool.characteristic || "—"}</span>
              <span>{pool.barcode || "нет баркода"}</span>
              <span>{pool.on_hand}</span>
              <span />
            </div>
          ))}
        </section>
      )}
    </>
  );
}
