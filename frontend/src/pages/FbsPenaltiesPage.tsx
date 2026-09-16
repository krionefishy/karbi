import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError } from "../api/http";
import { AppHeader } from "../components/AppHeader";
import { ConnectSellerDialog } from "../components/ConnectSellerDialog";
import { FbsPenaltiesLookup } from "../components/FbsPenaltiesLookup";
import { FbsPenaltiesTable } from "../components/FbsPenaltiesTable";
import { ConfirmDialog } from "../components/SellerDialog";
import { SellerSidebar } from "../components/SellerSidebar";
import {
  downloadPenalties,
  getPenalties,
  getRefreshState,
  lookupPenalties,
  requestRefresh,
} from "../features/fbsPenalties/api";
import { defaultPeriod, money } from "../features/fbsPenalties/period";
import type { DeductionGroup, GroupTotal, Lookup, PenaltiesFilter } from "../features/fbsPenalties/types";
import {
  attachSeller,
  detachSeller,
  getAutomationSellers,
  getSellers,
  retrySellerSync,
} from "../features/sellers/api";
import type { Seller, SellerInput } from "../features/sellers/types";
import { staleRefreshError } from "../features/refresh";

const AUTOMATION_ID = "wb-fbs-penalties";
const AUTOMATION_TITLE = "Штрафы FBS";

const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });

type Tab = "table" | "lookup";

const GROUP_OPTIONS: { value: DeductionGroup | ""; label: string }[] = [
  { value: "", label: "Все удержания" },
  { value: "penalties", label: "Штрафы" },
  { value: "deductions", label: "Удержания и доплаты" },
  { value: "logistics", label: "Логистика" },
  { value: "storage", label: "Хранение и приёмка" },
];

/** Строк в группе за период; пустая группа — все вместе. */
function groupCount(totals: GroupTotal[], group: DeductionGroup | ""): number {
  return totals.filter((item) => !group || item.group === group).reduce((sum, item) => sum + item.count, 0);
}

export function FbsPenaltiesPage() {
  const queryClient = useQueryClient();
  const [sellerId, setSellerId] = useState("");
  const [tab, setTab] = useState<Tab>("table");
  const [filter, setFilter] = useState<PenaltiesFilter>({ ...defaultPeriod(), group: "", warehouseId: null, page: 1 });
  /** Любая смена фильтра возвращает на первую страницу: нумерация старого списка к новому не относится. */
  const changeFilter = (patch: Partial<PenaltiesFilter>) => setFilter({ ...filter, ...patch, page: 1 });
  const [connecting, setConnecting] = useState(false);
  const [detaching, setDetaching] = useState<Seller | null>(null);
  const [formError, setFormError] = useState("");
  const [actionError, setActionError] = useState("");
  const [lookup, setLookup] = useState<Lookup | null>(null);
  const invalidRange = filter.dateFrom > filter.dateTo;
  const penaltiesKey = ["fbs-penalties", sellerId, filter];

  const { data: sellers = [], isLoading } = useQuery({
    queryKey: ["automation-sellers", AUTOMATION_ID],
    queryFn: () => getAutomationSellers(AUTOMATION_ID),
  });
  const { data: registry = [] } = useQuery({
    queryKey: ["wb-sellers", false],
    queryFn: () => getSellers(),
    enabled: connecting,
  });
  const { data: penalties, isLoading: penaltiesLoading } = useQuery({
    queryKey: penaltiesKey,
    queryFn: () => getPenalties(sellerId, filter),
    enabled: Boolean(sellerId) && !invalidRange,
  });

  useEffect(() => {
    if (!sellers.some((item) => item.id === sellerId)) {
      setSellerId(sellers[0]?.id ?? "");
    }
  }, [sellerId, sellers]);
  useEffect(() => {
    setActionError("");
    setLookup(null);
  }, [sellerId, tab]);

  const refreshSellers = async () => {
    await queryClient.invalidateQueries({ queryKey: ["automation-sellers", AUTOMATION_ID] });
    await queryClient.invalidateQueries({ queryKey: ["wb-sellers"] });
  };
  const invalidatePenalties = () => queryClient.invalidateQueries({ queryKey: ["fbs-penalties", sellerId] });
  const fail = (fallback: string) => (error: unknown) =>
    setActionError(error instanceof ApiError ? error.message : fallback);

  const connectMutation = useMutation({
    mutationFn: (payload: { seller_id: string } | SellerInput) => attachSeller(AUTOMATION_ID, payload),
    onSuccess: async (seller) => {
      setConnecting(false);
      setFormError("");
      setSellerId(seller.id);
      await refreshSellers();
    },
    onError: (error) => setFormError(error instanceof ApiError ? error.message : "Не удалось подключить селлера"),
  });
  const detachMutation = useMutation({
    mutationFn: (seller: Seller) => detachSeller(AUTOMATION_ID, seller.id),
    onSuccess: async () => {
      setDetaching(null);
      setSellerId("");
      await refreshSellers();
    },
  });
  const retryMutation = useMutation({ mutationFn: retrySellerSync, onSuccess: refreshSellers });

  // Пока идёт сбор отчёта, состояние опрашивается; таблица обновится сама, как только он закончится.
  const { data: refreshState } = useQuery({
    queryKey: ["fbs-penalties-refresh", sellerId],
    queryFn: () => getRefreshState(sellerId),
    enabled: Boolean(sellerId),
    refetchInterval: (query) => (query.state.data?.in_progress ? 3000 : false),
  });
  const refreshing = Boolean(refreshState?.in_progress);
  const refreshError = staleRefreshError(refreshState, penalties?.collected_at);
  useEffect(() => {
    if (refreshState?.status === "success") void invalidatePenalties();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshState?.status, refreshState?.finished_at, sellerId]);
  const refreshMutation = useMutation({
    mutationFn: () => requestRefresh(sellerId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["fbs-penalties-refresh", sellerId] }),
    onError: fail("Не удалось поставить обновление в очередь"),
  });
  const exportMutation = useMutation({
    mutationFn: () => downloadPenalties(sellerId, filter),
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename ?? "fbs_penalties.xlsx";
      link.click();
      URL.revokeObjectURL(url);
      setActionError("");
    },
    onError: fail("Не удалось выгрузить таблицу"),
  });
  const lookupMutation = useMutation({
    mutationFn: (text: string) => lookupPenalties(sellerId, text),
    onSuccess: (result) => {
      setActionError("");
      setLookup(result);
    },
    onError: fail("Не удалось проверить номера"),
  });

  const selected = sellers.find((seller) => seller.id === sellerId);
  const total = penalties?.totals.reduce((sum, item) => sum + item.amount, 0) ?? 0;

  return (
    <div className="app-page reviews-shell">
      <AppHeader current={AUTOMATION_TITLE} />
      <div className="reviews-workspace">
        <SellerSidebar
          sellers={sellers}
          selectedId={sellerId}
          onSelect={setSellerId}
          onAdd={() => {
            setFormError("");
            setConnecting(true);
          }}
          onDetach={setDetaching}
          onRetry={(seller) => retryMutation.mutate(seller.id)}
        />
        <main className="reviews-content">
          <div className="reviews-heading">
            <div>
              <p className="eyebrow">Wildberries / FBS</p>
              <h1>{AUTOMATION_TITLE}</h1>
              <p className="muted">
                {selected
                  ? `Удержания из фин. отчёта ${selected.name}: к каждой строке — склад продавца и поставка.`
                  : "Штрафы и удержания фин. отчёта по кабинету."}
              </p>
            </div>
            <div className="review-sync-actions">
              <span className="mode-switch">
                <button className={tab === "table" ? "mode-active" : ""} onClick={() => setTab("table")}>
                  Штрафы
                </button>
                <button className={tab === "lookup" ? "mode-active" : ""} onClick={() => setTab("lookup")} disabled={!selected}>
                  Проверить номера
                </button>
              </span>
              {selected && (
                <>
                  <div className="last-sync">
                    <span className="eyebrow">Отчёт от</span>
                    <strong>
                      {penalties?.collected_at ? momentFormatter.format(new Date(penalties.collected_at)) : "—"}
                    </strong>
                  </div>
                  <button
                    className="secondary-button"
                    disabled={refreshing || refreshMutation.isPending}
                    onClick={() => refreshMutation.mutate()}
                    title="Перечитать фин. отчёт прямо сейчас"
                  >
                    <RefreshCw size={15} className={refreshing ? "spinning" : undefined} />
                    {refreshing ? "Обновляем…" : "Обновить данные"}
                  </button>
                  <button
                    className="primary-button"
                    disabled={exportMutation.isPending || invalidRange}
                    onClick={() => exportMutation.mutate()}
                    title="Книга по кабинету за выбранный период"
                  >
                    <Download size={15} />
                    {exportMutation.isPending ? "Собираем…" : "Выгрузить в Excel"}
                  </button>
                </>
              )}
            </div>
          </div>

          {actionError && <div className="inline-error">{actionError}</div>}
          {refreshError && <div className="inline-error">Обновление не удалось: {refreshError}</div>}
          {penalties?.collection_error && (
            <div className="checklist-notice">Последний сбор: {penalties.collection_error}</div>
          )}

          {isLoading ? (
            <div className="loading-block">Загружаем данные…</div>
          ) : !selected ? (
            <div className="empty-state">
              <h2>Подключите первый кабинет</h2>
              <p>
                Возьмите селлера из реестра — фин. отчёт за три месяца подтянется сам, а к каждому штрафу
                встанут склад продавца и поставка.
              </p>
              <button
                className="primary-button"
                onClick={() => {
                  setFormError("");
                  setConnecting(true);
                }}
              >
                Подключить селлера
              </button>
            </div>
          ) : tab === "lookup" ? (
            <FbsPenaltiesLookup
              result={lookup}
              pending={lookupMutation.isPending}
              onLookup={(text) => lookupMutation.mutate(text)}
            />
          ) : (
            <>
              <div className="checklist-toolbar penalties-toolbar">
                <label className="penalties-field">
                  <span className="field-label">С</span>
                  <input
                    type="date"
                    value={filter.dateFrom}
                    onChange={(event) => changeFilter({ dateFrom: event.target.value })}
                  />
                </label>
                <label className="penalties-field">
                  <span className="field-label">По</span>
                  <input
                    type="date"
                    value={filter.dateTo}
                    onChange={(event) => changeFilter({ dateTo: event.target.value })}
                  />
                </label>
                <label className="penalties-field">
                  <span className="field-label">Склад продавца</span>
                  <select
                    value={filter.warehouseId ?? ""}
                    onChange={(event) =>
                      changeFilter({ warehouseId: event.target.value ? Number(event.target.value) : null })
                    }
                  >
                    <option value="">Все склады</option>
                    {(penalties?.warehouses ?? []).map((warehouse) => (
                      <option key={warehouse.warehouse_id} value={warehouse.warehouse_id}>
                        {warehouse.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="mode-switch penalties-groups" role="tablist" aria-label="Группы удержаний">
                {GROUP_OPTIONS.map((option) => {
                  const count = groupCount(penalties?.totals ?? [], option.value);
                  return (
                    <button
                      key={option.value}
                      type="button"
                      role="tab"
                      aria-selected={filter.group === option.value}
                      className={filter.group === option.value ? "mode-active" : undefined}
                      onClick={() => changeFilter({ group: option.value })}
                    >
                      {option.label}
                      {penalties && <small>{count}</small>}
                    </button>
                  );
                })}
              </div>
              {invalidRange ? (
                <p className="form-error" role="alert">
                  Начало периода позже его конца
                </p>
              ) : penaltiesLoading || !penalties ? (
                <div className="loading-block">Загружаем данные…</div>
              ) : (
                <>
                  <div className="penalties-totals" aria-label="Итоги по группам">
                    {penalties.totals.map((item) => (
                      <article key={item.group}>
                        <span>{item.title}</span>
                        <strong>{money(item.amount)}</strong>
                        <small>{item.count} стр.</small>
                      </article>
                    ))}
                    {penalties.totals.length > 1 && (
                      <article className="penalties-total-all">
                        <span>Всего</span>
                        <strong>{money(total)}</strong>
                        <small>{groupCount(penalties.totals, "")} стр.</small>
                      </article>
                    )}
                  </div>
                  <FbsPenaltiesTable
                    rows={penalties.rows}
                    empty={
                      penalties.collected_at
                        ? "За этот период удержаний нет."
                        : "Отчёт ещё не собран: подождите ближайшего сбора или нажмите «Обновить данные»."
                    }
                  />
                  {penalties.total_rows > penalties.page_size && (
                    <nav className="penalties-pager" aria-label="Страницы">
                      <button
                        type="button"
                        className="secondary-button"
                        disabled={filter.page <= 1}
                        onClick={() => setFilter({ ...filter, page: filter.page - 1 })}
                      >
                        <ChevronLeft size={15} /> Назад
                      </button>
                      <span>
                        {(penalties.page - 1) * penalties.page_size + 1}–
                        {Math.min(penalties.page * penalties.page_size, penalties.total_rows)} из {penalties.total_rows}
                      </span>
                      <button
                        type="button"
                        className="secondary-button"
                        disabled={penalties.page * penalties.page_size >= penalties.total_rows}
                        onClick={() => setFilter({ ...filter, page: filter.page + 1 })}
                      >
                        Вперёд <ChevronRight size={15} />
                      </button>
                    </nav>
                  )}
                </>
              )}
            </>
          )}
        </main>
      </div>

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
          title="Отключить от автоматизации?"
          description={`«${detaching.name}» перестанет собираться. Собранные строки останутся до повторного подключения.`}
          confirmLabel="Отключить"
          pendingLabel="Отключаем…"
          pending={detachMutation.isPending}
          onClose={() => setDetaching(null)}
          onConfirm={() => detachMutation.mutate(detaching)}
        />
      )}
    </div>
  );
}
