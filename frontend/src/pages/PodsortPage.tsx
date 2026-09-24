import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Plus, Save, Unplug } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError } from "../api/http";
import { ConnectSellerDialog } from "../components/ConnectSellerDialog";
import { ConfirmDialog } from "../components/SellerDialog";
import { Shell } from "../components/Shell";
import { downloadPodsort, getPodsort, saveSettings, setWarehouseRegion } from "../features/podsort/api";
import { coverLabel, sellerProblem, shortDay } from "../features/podsort/format";
import type { PodsortSettingsInput, PodsortWarehouse } from "../features/podsort/types";
import { attachSeller, detachSeller, getAutomationSellers, getSellers } from "../features/sellers/api";
import type { Seller, SellerInput } from "../features/sellers/types";

const AUTOMATION_ID = "wb-podsort";
const AUTOMATION_TITLE = "Подсорт WB";

const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });

const TEMPLATE = ["170px", "300px", "150px", "110px", "90px", "110px", "100px", "120px"].join(" ");

const SOURCE_LABELS: Record<PodsortWarehouse["source"], string> = {
  manual: "задан вручную",
  guess: "по городу в названии",
  unplaced: "WB не говорит, где лежит",
  none: "регион неизвестен",
};

const GUESS = "__guess__";
const NOWHERE = "__nowhere__";

function stamp(value: string | null): string {
  return value ? momentFormatter.format(new Date(value)) : "—";
}

export function PodsortPage() {
  const queryClient = useQueryClient();
  const [region, setRegion] = useState("");
  const [draft, setDraft] = useState<PodsortSettingsInput | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [detaching, setDetaching] = useState<Seller | null>(null);
  const [formError, setFormError] = useState("");
  const [actionError, setActionError] = useState("");

  const { data: sellers = [] } = useQuery({
    queryKey: ["automation-sellers", AUTOMATION_ID],
    queryFn: () => getAutomationSellers(AUTOMATION_ID),
  });
  const { data: registry = [] } = useQuery({
    queryKey: ["wb-sellers", false],
    queryFn: () => getSellers(),
    enabled: connecting,
  });
  const { data: podsort, isLoading, error: loadError } = useQuery({
    queryKey: ["wb-podsort", region],
    queryFn: () => getPodsort(region),
  });

  const settings = podsort?.settings;
  // Черновик настроек — пока его правят; сохранённое приходит с сервера.
  const form: PodsortSettingsInput | null =
    draft ?? (settings ? { window_days: settings.window_days, cover_days: settings.cover_days, regions: settings.regions } : null);
  useEffect(() => {
    if (settings && region && !settings.regions.includes(region)) setRegion("");
  }, [settings, region]);

  const fail = (fallback: string) => (error: unknown) =>
    setActionError(error instanceof ApiError ? error.message : fallback);
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["wb-podsort"] });
  };
  const refreshSellers = async () => {
    await queryClient.invalidateQueries({ queryKey: ["automation-sellers", AUTOMATION_ID] });
    await queryClient.invalidateQueries({ queryKey: ["wb-sellers"] });
    await refresh();
  };

  const settingsMutation = useMutation({
    mutationFn: saveSettings,
    onSuccess: async () => {
      setDraft(null);
      setActionError("");
      await refresh();
    },
    onError: fail("Не удалось сохранить настройки"),
  });
  const warehouseMutation = useMutation({
    mutationFn: ({ name, value }: { name: string; value: string }) =>
      setWarehouseRegion(name, value === GUESS || value === NOWHERE ? null : value, value === GUESS),
    onSuccess: refresh,
    onError: fail("Не удалось сохранить регион склада"),
  });
  const exportMutation = useMutation({
    mutationFn: downloadPodsort,
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename ?? "podsort.xlsx";
      link.click();
      URL.revokeObjectURL(url);
      setActionError("");
    },
    onError: fail("Не удалось выгрузить книгу"),
  });
  const connectMutation = useMutation({
    mutationFn: (payload: { seller_id: string } | SellerInput) => attachSeller(AUTOMATION_ID, payload),
    onSuccess: async () => {
      setConnecting(false);
      setFormError("");
      await refreshSellers();
    },
    onError: (error) => setFormError(error instanceof ApiError ? error.message : "Не удалось подключить селлера"),
  });
  const detachMutation = useMutation({
    mutationFn: (seller: Seller) => detachSeller(AUTOMATION_ID, seller.id),
    onSuccess: async () => {
      setDetaching(null);
      await refreshSellers();
    },
  });

  const problems = (podsort?.sellers ?? [])
    .map((state) => ({ name: state.name, text: settings ? sellerProblem(state, settings.window_days) : null }))
    .filter((item) => item.text);
  const unknownWarehouses = (podsort?.warehouses ?? []).filter((item) => item.source === "none" && item.quantity > 0);
  const toggleRegion = (name: string) => {
    if (!form) return;
    const regions = form.regions.includes(name) ? form.regions.filter((item) => item !== name) : [...form.regions, name];
    setDraft({ ...form, regions });
  };
  const dirty =
    draft !== null &&
    settings !== undefined &&
    (draft.window_days !== settings.window_days ||
      draft.cover_days !== settings.cover_days ||
      draft.regions.join("|") !== settings.regions.join("|"));

  return (
    <Shell current={AUTOMATION_TITLE}>
      <main className="reviews-content">
        <div className="reviews-heading">
          <div>
            <h1>{AUTOMATION_TITLE}</h1>
            <p className="muted">
              Сколько везти на склады WB: продажи региона на срок покрытия минус то, что уже лежит на его складах.
              Все подключённые кабинеты в одной книге.
            </p>
          </div>
          <div className="review-sync-actions">
            {podsort && (
              <div className="last-sync">
                <span className="eyebrow">Продажи за</span>
                <strong>
                  {shortDay(podsort.window_start)}–{shortDay(podsort.last_day)}
                </strong>
              </div>
            )}
            <button
              className="primary-button"
              disabled={exportMutation.isPending || sellers.length === 0}
              onClick={() => exportMutation.mutate()}
              title="Сводный лист по регионам с ручными столбцами для логиста и склада, лист на каждый кабинет"
            >
              <Download size={15} />
              {exportMutation.isPending ? "Собираем…" : "Выгрузить в Excel"}
            </button>
          </div>
        </div>

        {actionError && <div className="inline-error">{actionError}</div>}
        {loadError && (
          <div className="inline-error">{loadError instanceof ApiError ? loadError.message : "Не удалось загрузить подсорт"}</div>
        )}
        {problems.map((item) => (
          <div key={item.name} className="checklist-notice">
            {item.name}: {item.text}
          </div>
        ))}
        {unknownWarehouses.length > 0 && (
          <div className="checklist-notice">
            Склады без региона — их остаток не вычитается: {unknownWarehouses.map((item) => item.name).join(", ")}.
            Регион задаётся ниже, в «Склады WB».
          </div>
        )}

        {isLoading || !podsort || !form || !settings ? (
          <div className="loading-block">Загружаем данные…</div>
        ) : sellers.length === 0 ? (
          <div className="empty-state">
            <h2>Подключите первый кабинет</h2>
            <p>
              Заказы за три месяца подтянутся сами — сначала последние дни, чтобы расчёт появился быстрее. Остатки по
              складам WB собираются для всех кабинетов раз в шесть часов.
            </p>
            <button className="primary-button" onClick={() => setConnecting(true)}>
              Подключить селлера
            </button>
          </div>
        ) : (
          <>
            <section className="card podsort-settings">
              <div className="checklist-toolbar">
                <label className="penalties-field">
                  <span className="field-label">Продажи за</span>
                  <select
                    value={form.window_days}
                    onChange={(event) => setDraft({ ...form, window_days: Number(event.target.value) })}
                  >
                    {settings.window_choices.map((days) => (
                      <option key={days} value={days}>
                        {days} дней
                      </option>
                    ))}
                  </select>
                </label>
                <label className="penalties-field">
                  <span className="field-label">Везём на, дней</span>
                  <input
                    type="number"
                    min={1}
                    max={settings.max_cover_days}
                    value={form.cover_days}
                    onChange={(event) => setDraft({ ...form, cover_days: Number(event.target.value) })}
                  />
                </label>
                <fieldset className="podsort-regions">
                  <legend className="field-label">Регионы в сводном листе</legend>
                  {settings.available_regions.map((name) => (
                    <label key={name} className="podsort-region">
                      <input type="checkbox" checked={form.regions.includes(name)} onChange={() => toggleRegion(name)} />
                      {name}
                    </label>
                  ))}
                </fieldset>
                <button
                  className="secondary-button"
                  disabled={!dirty || settingsMutation.isPending || form.regions.length === 0}
                  onClick={() => settingsMutation.mutate(form)}
                >
                  <Save size={15} />
                  {settingsMutation.isPending ? "Сохраняем…" : "Сохранить"}
                </button>
              </div>
              <p className="muted podsort-formula">
                Расчёт системы = продажи за {settings.window_days} дн. / {settings.window_days} × {settings.cover_days} −
                остаток на складах WB региона; меньше нуля — 0. Остаток нашего склада не учитывается — наличие проверяет
                Ильвир. Склады ЦФО отгружают и в соседние регионы, поэтому смотрите «Дней покрытия».
              </p>
            </section>

            <div className="mode-switch penalties-groups" role="tablist" aria-label="Регионы">
              {settings.regions.map((name) => (
                <button
                  key={name}
                  type="button"
                  role="tab"
                  aria-selected={podsort.region === name}
                  className={podsort.region === name ? "mode-active" : undefined}
                  onClick={() => setRegion(name)}
                >
                  {name}
                  {podsort.region === name && <small>{podsort.rows.length}</small>}
                </button>
              ))}
            </div>

            <section className="checklist-scroll stocks-scroll" style={{ "--stocks-template": TEMPLATE } as React.CSSProperties}>
              <div className="stocks-head">
                <span className="checklist-sticky">Кабинет</span>
                <span>Товар</span>
                <span>Баркод</span>
                <span>Заказы за {settings.window_days} дн.</span>
                <span>Ср. в день</span>
                <span>Остаток WB в регионе</span>
                <span>Дней покрытия</span>
                <span>Расчёт системы</span>
              </div>
              {podsort.rows.length === 0 ? (
                <div className="stocks-row">
                  <span className="stocks-note-static">Везти нечего: остатка WB в регионе хватает на срок покрытия.</span>
                </div>
              ) : (
                podsort.rows.map((row) => (
                  <div className="stocks-row" key={`${row.seller_name}:${row.barcode}`}>
                    <span className="checklist-sticky">{row.seller_name}</span>
                    <span className="stocks-product">
                      <strong>{row.vendor_code || row.subject || "—"}</strong>
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
                        {row.tech_size && row.tech_size !== "0" ? ` · ${row.tech_size}` : ""}
                      </em>
                    </span>
                    <span className="stocks-barcode">{row.barcode}</span>
                    <span className="stocks-cell">{row.window_orders}</span>
                    <span className="stocks-cell">{row.average.toLocaleString("ru-RU")}</span>
                    <span className="stocks-cell">{row.stock}</span>
                    <span className="stocks-cell">{coverLabel(row.cover_days)}</span>
                    <span className="stocks-cell stocks-cell-group">{row.need}</span>
                  </div>
                ))
              )}
            </section>

            <section className="card podsort-sellers">
              <div className="checklist-toolbar stocks-toolbar">
                <strong>Кабинеты</strong>
                <button className="secondary-button" onClick={() => setConnecting(true)}>
                  <Plus size={15} /> Подключить кабинет
                </button>
              </div>
              {podsort.sellers.map((state) => {
                const seller = sellers.find((item) => item.id === state.seller_id);
                return (
                  <div key={state.seller_id} className="stocks-warehouse-row podsort-seller-row">
                    <span>
                      <strong>{state.name}</strong>
                      <em className="muted">
                        {" "}
                        · заказы с {state.history_from ? shortDay(state.history_from) : "—"}, {state.history_days_loaded} дн.
                      </em>
                    </span>
                    <span className="muted">Остатки WB: {stamp(state.remains_at)}</span>
                    {seller && (
                      <button
                        className="secondary-button stocks-hidden-action"
                        onClick={() => setDetaching(seller)}
                        title="Отключить кабинет от подсорта"
                      >
                        <Unplug size={14} />
                      </button>
                    )}
                  </div>
                );
              })}
            </section>

            <details className="stocks-hidden podsort-warehouses" open={unknownWarehouses.length > 0}>
              <summary>Склады WB и их регионы — {podsort.warehouses.length}</summary>
              <ul className="stocks-hidden-list">
                {podsort.warehouses.map((warehouse) => (
                  <li key={warehouse.name} className="stocks-warehouse-row">
                    <span>
                      <strong>{warehouse.name}</strong>
                      <em className="muted">
                        {" "}
                        · {warehouse.quantity} шт. · {SOURCE_LABELS[warehouse.source]}
                      </em>
                    </span>
                    <select
                      aria-label={`Регион склада ${warehouse.name}`}
                      value={warehouse.source === "manual" ? (warehouse.region ?? NOWHERE) : GUESS}
                      disabled={warehouseMutation.isPending}
                      onChange={(event) => warehouseMutation.mutate({ name: warehouse.name, value: event.target.value })}
                    >
                      <option value={GUESS}>
                        {warehouse.source === "guess"
                          ? `По городу: ${warehouse.region}`
                          : warehouse.source === "unplaced"
                            ? "Без региона — WB не говорит, где лежит"
                            : "По городу — не узнали"}
                      </option>
                      <option value={NOWHERE}>Не относится ни к одному</option>
                      {settings.available_regions.map((name) => (
                        <option key={name} value={name}>
                          {name}
                        </option>
                      ))}
                    </select>
                    <span />
                  </li>
                ))}
              </ul>
            </details>
          </>
        )}
      </main>

      {connecting && (
        <ConnectSellerDialog
          automationTitle={AUTOMATION_TITLE}
          available={registry.filter((item) => !sellers.some((enrolled) => enrolled.id === item.id))}
          pending={connectMutation.isPending}
          error={formError}
          onClose={() => setConnecting(false)}
          onConnect={(payload) => connectMutation.mutate(payload)}
        />
      )}
      {detaching && (
        <ConfirmDialog
          title="Отключить от подсорта?"
          description={`«${detaching.name}» пропадёт из книги. Загруженные заказы останутся до повторного подключения.`}
          confirmLabel="Отключить"
          pendingLabel="Отключаем…"
          pending={detachMutation.isPending}
          onClose={() => setDetaching(null)}
          onConfirm={() => detachMutation.mutate(detaching)}
        />
      )}
    </Shell>
  );
}
