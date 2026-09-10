import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Download, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState, type CSSProperties } from "react";

import { ApiError } from "../api/http";
import { AppHeader } from "../components/AppHeader";
import { ConnectSellerDialog } from "../components/ConnectSellerDialog";
import { ConfirmDialog } from "../components/SellerDialog";
import { SellerSidebar } from "../components/SellerSidebar";
import {
  downloadChecklist,
  getChecklist,
  getRefreshState,
  requestRefresh,
  setComment,
  setMark,
} from "../features/checklist/api";
import { applyMark, cellHint, isLocked, kindLabel, notices, visibleRows } from "../features/checklist/table";
import type { Checklist, ChecklistItem, ChecklistRow, ItemState } from "../features/checklist/types";
import {
  attachSeller,
  detachSeller,
  getAutomationSellers,
  getSellers,
  retrySellerSync,
} from "../features/sellers/api";
import type { Seller, SellerInput } from "../features/sellers/types";

const AUTOMATION_ID = "wb-card-checklist";
const AUTOMATION_TITLE = "Чек-лист карточек Wildberries";

const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });

interface MarkChange {
  article: string;
  item: string;
  checked: boolean;
}

export function CardChecklistPage() {
  const queryClient = useQueryClient();
  const [sellerId, setSellerId] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [detaching, setDetaching] = useState<Seller | null>(null);
  const [formError, setFormError] = useState("");
  const [actionError, setActionError] = useState("");
  const [search, setSearch] = useState("");
  const [onlyPending, setOnlyPending] = useState(false);
  const checklistKey = ["card-checklist", sellerId];

  const { data: sellers = [], isLoading } = useQuery({
    queryKey: ["automation-sellers", AUTOMATION_ID],
    queryFn: () => getAutomationSellers(AUTOMATION_ID),
  });
  const { data: registry = [] } = useQuery({
    queryKey: ["wb-sellers", false],
    queryFn: () => getSellers(),
    enabled: connecting,
  });
  const { data: checklist, isLoading: checklistLoading } = useQuery({
    queryKey: checklistKey,
    queryFn: () => getChecklist(sellerId),
    enabled: Boolean(sellerId),
  });

  useEffect(() => {
    if (!sellers.some((item) => item.id === sellerId)) {
      setSellerId(sellers[0]?.id ?? "");
    }
  }, [sellerId, sellers]);
  useEffect(() => setActionError(""), [sellerId]);

  const refreshSellers = async () => {
    await queryClient.invalidateQueries({ queryKey: ["automation-sellers", AUTOMATION_ID] });
    await queryClient.invalidateQueries({ queryKey: ["wb-sellers"] });
  };
  const connectMutation = useMutation({
    mutationFn: (payload: { seller_id: string } | SellerInput) => attachSeller(AUTOMATION_ID, payload),
    onSuccess: async (seller) => {
      setConnecting(false);
      setFormError("");
      setSellerId(seller.id);
      await refreshSellers();
    },
    onError: (error) =>
      setFormError(error instanceof ApiError ? error.message : "Не удалось подключить селлера"),
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

  // Пока идёт сбор, состояние опрашивается; таблица обновится сама, как только он закончится.
  const { data: refreshState } = useQuery({
    queryKey: ["card-checklist-refresh", sellerId],
    queryFn: () => getRefreshState(sellerId),
    enabled: Boolean(sellerId),
    refetchInterval: (query) => (query.state.data?.in_progress ? 3000 : false),
  });
  const refreshing = Boolean(refreshState?.in_progress);
  useEffect(() => {
    if (refreshState?.status === "success") {
      void queryClient.invalidateQueries({ queryKey: ["card-checklist", sellerId] });
    }
  }, [queryClient, refreshState?.status, refreshState?.finished_at, sellerId]);
  const refreshMutation = useMutation({
    mutationFn: () => requestRefresh(sellerId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["card-checklist-refresh", sellerId] });
    },
  });

  const exportMutation = useMutation({
    mutationFn: () => downloadChecklist(sellerId),
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename ?? "checklist.xlsx";
      link.click();
      URL.revokeObjectURL(url);
      setActionError("");
    },
    onError: (error) => setActionError(error instanceof ApiError ? error.message : "Не удалось выгрузить таблицу"),
  });

  // Галочка видна сразу, не дожидаясь ответа; отказ сервера откатывает её обратно.
  const markMutation = useMutation({
    mutationFn: ({ article, item, checked }: MarkChange) => setMark(sellerId, article, item, checked),
    onMutate: async ({ article, item, checked }: MarkChange) => {
      await queryClient.cancelQueries({ queryKey: checklistKey });
      const previous = queryClient.getQueryData<Checklist>(checklistKey);
      if (previous) queryClient.setQueryData(checklistKey, applyMark(previous, article, item, checked));
      return { previous };
    },
    onError: (error, _change, context) => {
      if (context?.previous) queryClient.setQueryData(checklistKey, context.previous);
      setActionError(error instanceof ApiError ? error.message : "Не удалось сохранить отметку");
    },
    onSuccess: () => setActionError(""),
    onSettled: () => queryClient.invalidateQueries({ queryKey: checklistKey }),
  });
  const commentMutation = useMutation({
    mutationFn: ({ article, text }: { article: string; text: string }) => setComment(sellerId, article, text),
    onError: (error) =>
      setActionError(error instanceof ApiError ? error.message : "Не удалось сохранить комментарий"),
    onSettled: () => queryClient.invalidateQueries({ queryKey: checklistKey }),
  });

  const selected = sellers.find((seller) => seller.id === sellerId);
  const rows = checklist?.rows ?? [];
  const shown = useMemo(() => visibleRows(rows, { search, onlyPending }), [rows, search, onlyPending]);
  const ready = rows.filter((row) => row.ready).length;
  const warnings = checklist ? notices(checklist) : [];

  return (
    <div className="app-page reviews-shell">
      <AppHeader current="Чек-лист карточек" />
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
              <p className="eyebrow">Wildberries / карточки</p>
              <h1>Чек-лист карточек</h1>
              <p className="muted">
                Карточки с остатком от {checklist?.min_stock ?? 10} шт.
                {selected ? ` у ${selected.name}` : ""}. Что видно в данных WB, отмечается само, остальное —
                менеджер.
              </p>
            </div>
            {selected && (
              <div className="review-sync-actions">
                <div className="last-sync">
                  <span className="eyebrow">Готовы</span>
                  <strong>
                    {ready} из {rows.length}
                  </strong>
                </div>
                <div className="last-sync">
                  <span className="eyebrow">Карточки от</span>
                  <strong>
                    {checklist?.collected_at ? momentFormatter.format(new Date(checklist.collected_at)) : "—"}
                  </strong>
                </div>
                <button
                  className="secondary-button"
                  disabled={refreshing || refreshMutation.isPending}
                  onClick={() => refreshMutation.mutate()}
                  title="Перечитать карточки и цены прямо сейчас"
                >
                  <RefreshCw size={15} className={refreshing ? "spinning" : undefined} />
                  {refreshing ? "Обновляем…" : "Обновить данные"}
                </button>
                <button
                  className="primary-button"
                  disabled={exportMutation.isPending || rows.length === 0}
                  onClick={() => exportMutation.mutate()}
                  title="Таблица в формате ручного чек-листа"
                >
                  <Download size={15} />
                  {exportMutation.isPending ? "Собираем…" : "Выгрузить в Excel"}
                </button>
              </div>
            )}
          </div>

          {actionError && <div className="inline-error">{actionError}</div>}
          {refreshState?.status === "error" && !refreshing && (
            <div className="inline-error">Обновление не удалось: {refreshState.error ?? "неизвестная ошибка"}</div>
          )}
          {checklist?.collection_error && (
            <div className="checklist-notice">Последний сбор: {checklist.collection_error}</div>
          )}
          {warnings.map((text) => (
            <div className="checklist-notice" key={text}>
              {text}
            </div>
          ))}

          {isLoading || checklistLoading ? (
            <div className="loading-block">Загружаем данные…</div>
          ) : !selected ? (
            <div className="empty-state">
              <h2>Подключите первого селлера</h2>
              <p>
                Возьмите селлера из реестра — карточки и цены прочитаются при ближайшем сборе, а остатки и отзывы
                подтянутся из «Оборачиваемости» и «Мониторинга отзывов».
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
          ) : !checklist?.collected_at ? (
            <div className="empty-state">
              <h2>Карточки ещё не прочитаны</h2>
              <p>Первый сбор пройдёт по расписанию. Чтобы не ждать, нажмите «Обновить данные».</p>
            </div>
          ) : rows.length === 0 ? (
            <div className="empty-state">
              <h2>Нет товаров с остатком от {checklist.min_stock} шт.</h2>
              <p>Как только товар наберёт остаток, он появится здесь вместе со своими отметками.</p>
            </div>
          ) : (
            <>
              <div className="checklist-toolbar">
                <input
                  type="search"
                  aria-label="Поиск по товарам"
                  placeholder="Артикул, баркод или название"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
                <label className="dormant-toggle">
                  <input
                    type="checkbox"
                    checked={onlyPending}
                    onChange={(event) => setOnlyPending(event.target.checked)}
                  />
                  Только неготовые ({rows.length - ready})
                </label>
              </div>
              <section
                className="checklist-scroll"
                style={{ "--checklist-items": checklist.items.length } as CSSProperties}
              >
                <div className="checklist-head">
                  <span className="checklist-sticky">Товар</span>
                  <span>Остаток</span>
                  {checklist.items.map((item) => (
                    <span key={item.key} title={item.meaning}>
                      {item.title}
                      <em className={`checklist-kind checklist-kind-${item.kind}`}>{kindLabel(item.kind)}</em>
                    </span>
                  ))}
                  <span>Готово</span>
                  <span>Комментарий</span>
                </div>
                {shown.map((row) => (
                  <ChecklistRowView
                    key={row.article}
                    row={row}
                    items={checklist.items}
                    onToggle={(item, checked) => markMutation.mutate({ article: row.article, item, checked })}
                    onComment={(text) => commentMutation.mutate({ article: row.article, text })}
                  />
                ))}
              </section>
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
          description={`«${detaching.name}» перестанет попадать в сбор карточек. Селлер и отметки менеджеров останутся.`}
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

interface RowProps {
  row: ChecklistRow;
  items: ChecklistItem[];
  onToggle: (item: string, checked: boolean) => void;
  onComment: (text: string) => void;
}

function ChecklistRowView({ row, items, onToggle, onComment }: RowProps) {
  const states = new Map(row.items.map((state) => [state.key, state]));
  return (
    <div className="checklist-row">
      <div className="article-title checklist-sticky">
        {row.photo_url ? (
          <img className="product-photo" src={row.photo_url} alt="" loading="lazy" />
        ) : (
          <span className="product-photo product-photo-empty" aria-hidden="true" />
        )}
        <span>
          <strong>{row.title || "Без названия"}</strong>
          <em>
            <a
              className="wb-article-link"
              href={`https://www.wildberries.ru/catalog/${row.article}/detail.aspx`}
              target="_blank"
              rel="noreferrer"
            >
              {row.article}
            </a>
            {row.vendor_code && ` · ${row.vendor_code}`}
          </em>
        </span>
      </div>
      <span className="checklist-number">{row.stock}</span>
      {items.map((item) => {
        const state = states.get(item.key);
        return state ? (
          <ItemCell key={item.key} item={item} state={state} onToggle={(checked) => onToggle(item.key, checked)} />
        ) : (
          <span key={item.key} />
        );
      })}
      <span className={`checklist-number ${row.ready ? "checklist-ready" : "checklist-pending"}`}>
        {row.done}/{row.items.length}
      </span>
      <div className="checklist-comment">
        <input
          // Ключ с текстом: после ответа сервера поле берёт сохранённое значение.
          key={`${row.article}:${row.comment}`}
          aria-label={`Комментарий к ${row.article}`}
          defaultValue={row.comment}
          maxLength={2000}
          placeholder="—"
          onBlur={(event) => {
            if (event.target.value !== row.comment) onComment(event.target.value);
          }}
        />
      </div>
    </div>
  );
}

interface CellProps {
  item: ChecklistItem;
  state: ItemState;
  onToggle: (checked: boolean) => void;
}

function ItemCell({ item, state, onToggle }: CellProps) {
  const classes = ["checklist-cell"];
  if (state.checked) classes.push("checklist-cell-done");
  if (state.warning) classes.push("checklist-cell-warning");
  if (state.unknown) classes.push("checklist-cell-unknown");
  return (
    <div className={classes.join(" ")} title={cellHint(item, state)}>
      <input
        type="checkbox"
        aria-label={`${item.title}`}
        checked={state.checked}
        disabled={isLocked(state)}
        onChange={(event) => onToggle(event.target.checked)}
      />
      {state.warning && <AlertTriangle size={12} className="checklist-warning" aria-label={state.warning} />}
      <small>{state.detail ?? (state.unknown ? "нет данных" : "")}</small>
    </div>
  );
}
