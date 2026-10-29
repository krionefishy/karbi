import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertOctagon, AlertTriangle, Check, Copy, Link2, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { ApiError } from "../api/http";
import { ConnectSellerDialog } from "../components/ConnectSellerDialog";
import { ConfirmDialog } from "../components/SellerDialog";
import { SellerSwitcher } from "../components/SellerSwitcher";
import { Shell } from "../components/Shell";
import {
  getCardDocuments,
  getInviteLink,
  getRefreshState,
  inviteErrorMessage,
  requestRefresh,
} from "../features/cardDocuments/api";
import {
  banners,
  daysLeftText,
  daysTone,
  documentPeriod,
  emptyMessage,
  hasStock,
  reasonLabel,
  stateLabel,
  stateTone,
  stockText,
  tileSummary,
  tiles,
  visibleItems,
  type DocumentFilter,
} from "../features/cardDocuments/table";
import type { CardDocumentItem, InviteLink } from "../features/cardDocuments/types";
import { staleRefreshError } from "../features/refresh";
import {
  attachSeller,
  detachSeller,
  getAutomationSellers,
  getSellers,
  retrySellerSync,
} from "../features/sellers/api";
import type { Seller, SellerInput } from "../features/sellers/types";

const AUTOMATION_ID = "wb-card-documents";
const AUTOMATION_TITLE = "Документы карточек WB";

const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });
const formatMoment = (iso: string) => momentFormatter.format(new Date(iso));

export function CardDocumentsPage() {
  const queryClient = useQueryClient();
  const [sellerId, setSellerId] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [detaching, setDetaching] = useState<Seller | null>(null);
  const [formError, setFormError] = useState("");
  const [filter, setFilter] = useState<DocumentFilter>("problems");
  const [search, setSearch] = useState("");
  const [onlyStock, setOnlyStock] = useState(true);
  const [invite, setInvite] = useState<InviteLink | null>(null);
  const [inviteCopied, setInviteCopied] = useState(false);

  const {
    data: sellers = [],
    isLoading,
    isError: sellersFailed,
  } = useQuery({
    queryKey: ["automation-sellers", AUTOMATION_ID],
    queryFn: () => getAutomationSellers(AUTOMATION_ID),
  });
  const { data: registry = [] } = useQuery({
    queryKey: ["wb-sellers", false],
    queryFn: () => getSellers(),
    enabled: connecting,
  });
  const {
    data: documents,
    isLoading: documentsLoading,
    error: documentsError,
    refetch,
  } = useQuery({
    queryKey: ["card-documents", sellerId],
    queryFn: () => getCardDocuments(sellerId),
    enabled: Boolean(sellerId),
  });

  useEffect(() => {
    if (!sellers.some((item) => item.id === sellerId)) {
      setSellerId(sellers[0]?.id ?? "");
    }
  }, [sellerId, sellers]);
  useEffect(() => {
    setFilter("problems");
    setSearch("");
    setInvite(null);
  }, [sellerId]);

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

  const { data: refreshState } = useQuery({
    queryKey: ["card-documents-refresh", sellerId],
    queryFn: () => getRefreshState(sellerId),
    enabled: Boolean(sellerId),
    refetchInterval: (query) => (query.state.data?.in_progress ? 3000 : false),
  });
  const refreshing = Boolean(refreshState?.in_progress);
  const refreshError = staleRefreshError(refreshState, documents?.collected_at);
  useEffect(() => {
    if (refreshState?.status === "success") {
      void queryClient.invalidateQueries({ queryKey: ["card-documents", sellerId] });
    }
  }, [queryClient, refreshState?.status, refreshState?.finished_at, sellerId]);
  const refreshMutation = useMutation({
    mutationFn: () => requestRefresh(sellerId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["card-documents-refresh", sellerId] });
    },
  });

  const inviteMutation = useMutation({
    mutationFn: () => getInviteLink(sellerId),
    onSuccess: (link) => {
      setInvite(link);
      setInviteCopied(false);
    },
  });

  const selected = sellers.find((seller) => seller.id === sellerId);
  const items = documents?.items ?? [];
  const shown = useMemo(() => visibleItems(items, filter, search, onlyStock), [items, filter, search, onlyStock]);
  const warnings = documents && selected ? banners(documents, true) : [];
  const requestError =
    refreshMutation.error instanceof ApiError ? refreshMutation.error.message : refreshMutation.error ? "Не удалось запустить обновление" : "";

  const openConnect = () => {
    setFormError("");
    setConnecting(true);
  };

  return (
    <Shell
      current="Документы карточек"
      seller={
        <SellerSwitcher
          sellers={sellers}
          selectedId={sellerId}
          onSelect={setSellerId}
          onAdd={openConnect}
          onDetach={setDetaching}
          onRetry={(seller) => retryMutation.mutate(seller.id)}
        />
      }
    >
      <main className="reviews-content">
        <div className="reviews-heading">
          <div>
            <h1>Документы карточек</h1>
            <p className="muted">
              Декларации и сертификаты, которые WB проверяет по реестру
              {selected ? ` — ${selected.name}` : ""}. Проблемные карточки сверху.
            </p>
          </div>
          {selected && (
            <div className="review-sync-actions">
              <div className="last-sync">
                <span className="eyebrow">Проверено</span>
                <strong>{documents?.collected_at ? formatMoment(documents.collected_at) : "—"}</strong>
              </div>
              <button
                className="secondary-button"
                disabled={refreshing || refreshMutation.isPending}
                onClick={() => refreshMutation.mutate()}
                title="Перечитать документы карточек прямо сейчас"
              >
                <RefreshCw size={15} className={refreshing ? "spinning" : undefined} />
                {refreshing ? "Обновляем…" : "Обновить данные"}
              </button>
              <button
                className="primary-button"
                disabled={inviteMutation.isPending}
                onClick={() => inviteMutation.mutate()}
                title="Ссылка, по которой сотрудник подпишется на уведомления о документах этого кабинета"
              >
                <Link2 size={15} />
                {inviteMutation.isPending ? "Готовим…" : "Ссылка на бота"}
              </button>
            </div>
          )}
        </div>

        {inviteMutation.isError && (
          <div className="inline-error" role="alert">
            {inviteErrorMessage(inviteMutation.error)}
          </div>
        )}
        {invite && (
          <div className="invite-banner">
            <div>
              <span className="eyebrow">Ссылка для подписки на уведомления о документах</span>
              <code>{invite.url}</code>
              <em>
                Действует до {formatMoment(invite.expires_at)}. Одноразовая: после подписки перестанет работать, выпуск
                новой гасит эту.
              </em>
            </div>
            <button
              className="secondary-button"
              onClick={() => {
                void navigator.clipboard?.writeText(invite.url);
                setInviteCopied(true);
              }}
            >
              <Copy size={15} />
              {inviteCopied ? "Скопировано" : "Копировать"}
            </button>
          </div>
        )}
        {requestError && <div className="inline-error">{requestError}</div>}
        {refreshError && <div className="inline-error">Обновление не удалось: {refreshError}</div>}
        {warnings.length > 0 && (
          <div className="docs-banners">
            {warnings.map((item) => {
              const Icon = item.level === "danger" ? AlertOctagon : AlertTriangle;
              return (
                <div
                  key={item.key}
                  className={`docs-banner docs-banner-${item.level}`}
                  role={item.level === "danger" ? "alert" : "status"}
                >
                  <span className="docs-banner-mark" aria-hidden="true">
                    <Icon size={18} />
                  </span>
                  <div>
                    <strong>{item.title}</strong>
                    {item.text && <p>{item.text}</p>}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {isLoading || (Boolean(sellerId) && documentsLoading) ? (
          <div className="loading-block">Загружаем данные…</div>
        ) : sellersFailed ? (
          <div className="inline-error">Не удалось загрузить список селлеров. Обновите страницу.</div>
        ) : !selected ? (
          <div className="empty-state">
            <h2>Подключите первого селлера</h2>
            <p>Возьмите селлера из реестра — документы карточек проверятся при ближайшем сборе.</p>
            <button className="primary-button" onClick={openConnect}>
              Подключить селлера
            </button>
          </div>
        ) : documentsError || !documents ? (
          <div className="inline-error" role="alert">
            <div>
              <strong>Не удалось загрузить документы карточек</strong>
              {documentsError instanceof ApiError ? documentsError.message : "Сервер не ответил."}{" "}
              <button className="secondary-button docs-retry" onClick={() => void refetch()}>
                Повторить
              </button>
            </div>
          </div>
        ) : !documents.collected_at && items.length === 0 ? (
          <div className="empty-state">
            <h2>Документы ещё не проверяли</h2>
            <p>Первый сбор пройдёт по расписанию. Чтобы не ждать, нажмите «Обновить данные».</p>
          </div>
        ) : (
          <>
            <div className="docs-tiles" role="group" aria-label="Фильтр по состоянию">
              {tiles(tileSummary(documents, onlyStock)).map((tile) => (
                <button
                  key={tile.key}
                  type="button"
                  className={`docs-tile docs-tile-${tile.key}${filter === tile.key ? " docs-tile-active" : ""}`}
                  aria-pressed={filter === tile.key}
                  onClick={() => setFilter(filter === tile.key ? "problems" : tile.key)}
                >
                  <span>{tile.label}</span>
                  <strong>{tile.count}</strong>
                  {tile.note && <small>{tile.note}</small>}
                </button>
              ))}
            </div>
            <div className="checklist-toolbar">
              <input
                type="search"
                className="docs-search"
                aria-label="Поиск по карточкам"
                placeholder="nmID, артикул, название или номер документа"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              <label className="dormant-toggle" title="Уведомления приходят только по товарам с остатком">
                <input
                  type="checkbox"
                  checked={onlyStock}
                  onChange={(event) => setOnlyStock(event.target.checked)}
                />
                Только с остатком ({items.filter(hasStock).length} из {items.length})
              </label>
            </div>
            {shown.length === 0 ? (
              <div className="empty-state">
                <h2>{emptyMessage(filter, tileSummary(documents, onlyStock), search, onlyStock)}</h2>
              </div>
            ) : (
              <div className="docs-scroll">
                <table className="docs-table">
                  <thead>
                    <tr>
                      <th>Товар</th>
                      <th>nmID</th>
                      <th>Артикул</th>
                      <th title="Уведомления приходят только по товарам с остатком">Остаток</th>
                      <th>Состояние</th>
                      <th>Причина</th>
                      <th>Документ</th>
                      <th>Срок</th>
                      <th>С какого момента</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((item) => (
                      <DocumentRow key={item.nm_id} item={item} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
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
          title="Отключить от автоматизации?"
          description={`«${detaching.name}» перестанет попадать в проверку документов карточек. Селлер останется в реестре.`}
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

function DocumentRow({ item }: { item: CardDocumentItem }) {
  const period = documentPeriod(item.document_start, item.document_end);
  return (
    <tr className={`docs-row-${item.state}`}>
      <td className="docs-product">
        <strong>{item.title || "Без названия"}</strong>
        {item.subject_name && <small>{item.subject_name}</small>}
      </td>
      <td>
        <NmId nmId={item.nm_id} />
      </td>
      <td className="mono docs-vendor">{item.vendor_code || "—"}</td>
      <td
        className={`docs-stock${item.stock === 0 ? " docs-stock-empty" : ""}`}
        title="Уведомления приходят только по товарам с остатком"
      >
        {stockText(item.stock)}
      </td>
      <td>
        <span className={`docs-state docs-tone-${stateTone(item.state)}`}>{stateLabel(item.state)}</span>
      </td>
      <td className="docs-reason">{reasonLabel(item.reason)}</td>
      <td className="docs-document">
        <span className="mono">{item.document_number || "—"}</span>
        {period && <small>{period}</small>}
      </td>
      <td className={`docs-days docs-tone-${daysTone(item.days_left)}`}>{daysLeftText(item.days_left)}</td>
      <td className="docs-since">
        {item.since ? formatMoment(item.since) : "—"}
        {item.checked_at && <small>проверено {formatMoment(item.checked_at)}</small>}
      </td>
    </tr>
  );
}

function NmId({ nmId }: { nmId: number }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1500);
    return () => clearTimeout(timer);
  }, [copied]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(String(nmId));
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };
  return (
    <span className="docs-nm">
      <a
        className="wb-article-link mono"
        href={`https://www.wildberries.ru/catalog/${nmId}/detail.aspx`}
        target="_blank"
        rel="noreferrer"
      >
        {nmId}
      </a>
      <button
        type="button"
        className="ghost-button icon-button docs-copy"
        onClick={() => void copy()}
        aria-label={copied ? `nmID ${nmId} скопирован` : `Скопировать nmID ${nmId}`}
        title={copied ? "Скопировано" : "Скопировать nmID"}
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
      </button>
    </span>
  );
}
