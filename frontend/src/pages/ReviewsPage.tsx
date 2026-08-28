import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError } from "../api/http";
import { AppHeader } from "../components/AppHeader";
import { ArticleFilters } from "../components/ArticleFilters";
import { ReviewTimeline } from "../components/ReviewTimeline";
import { ConnectSellerDialog } from "../components/ConnectSellerDialog";
import { ConfirmDialog, SellerDialog } from "../components/SellerDialog";
import { SellerSidebar } from "../components/SellerSidebar";
import { ReviewReportDialog } from "../components/ReviewReportDialog";
import {
  downloadReviewReport,
  getLatestReviewSync,
  getSellerReviewHistory,
  startReviewSync,
} from "../features/reviews/api";
import {
  attachSeller,
  detachSeller,
  getAutomationSellers,
  getSellerArticles,
  getSellers,
  retrySellerSync,
  updateSeller,
} from "../features/sellers/api";
import { applyFilters, collectSubjects, hasActiveFilters } from "../features/reviews/filters";
import { latestCollectedDate, sortByMovement } from "../features/reviews/movement";
import type { ArticleFilters as Filters } from "../features/reviews/filters";
import type { ArticleState, Seller, SellerInput } from "../features/sellers/types";

const AUTOMATION_ID = "wb-reviews";
const AUTOMATION_TITLE = "Мониторинг отзывов Wildberries";

const articleStateText: Record<ArticleState, string> = {
  active: "В продаже",
  archived: "В архиве WB",
  feedback_only: "Нет в каталоге",
};

const syncStatusText: Record<Seller["catalog_sync_status"], string> = {
  queued: "В очереди",
  syncing: "Синхронизация",
  success: "Готово",
  error: "Ошибка",
};

function NothingFound({ onReset }: { onReset: () => void }) {
  return (
    <div className="empty-state">
      <h2>Ничего не найдено</h2>
      <p>Ни один товар не подходит под выбранный предмет и строку поиска.</p>
      <button className="primary-button" onClick={onReset}>
        Сбросить фильтры
      </button>
    </div>
  );
}

export function ReviewsPage() {
  const queryClient = useQueryClient();
  const { data: sellers = [], isLoading } = useQuery({
    queryKey: ["automation-sellers", AUTOMATION_ID],
    queryFn: () => getAutomationSellers(AUTOMATION_ID),
    refetchInterval: (query) =>
      query.state.data?.some(
        (item) => item.catalog_sync_status === "queued" || item.catalog_sync_status === "syncing",
      )
        ? 3000
        : false,
  });
  const [searchParams, setSearchParams] = useSearchParams();
  const filters: Filters = useMemo(
    () => ({ query: searchParams.get("q") ?? "", subjects: searchParams.getAll("subject") }),
    [searchParams],
  );
  const setFilters = (next: Filters) => {
    const params = new URLSearchParams(searchParams);
    params.delete("q");
    params.delete("subject");
    if (next.query.trim()) params.set("q", next.query);
    for (const subject of next.subjects) params.append("subject", subject);
    setSearchParams(params, { replace: true });
  };
  const [sellerId, setSellerId] = useState("");
  const [dialogSeller, setDialogSeller] = useState<Seller | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [detaching, setDetaching] = useState<Seller | null>(null);
  const [formError, setFormError] = useState("");
  const [reportOpen, setReportOpen] = useState(false);
  const [reportError, setReportError] = useState("");

  useEffect(() => {
    if (!sellers.some((item) => item.id === sellerId)) {
      setSellerId(sellers[0]?.id ?? "");
    }
  }, [sellerId, sellers]);

  useEffect(() => {
    // Subjects differ from seller to seller, so a chosen one stops meaning
    // anything after switching; the typed query still does.
    if (!sellerId) return;
    setSearchParams(
      (params) => {
        if (!params.has("subject")) return params;
        const next = new URLSearchParams(params);
        next.delete("subject");
        return next;
      },
      { replace: true },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sellerId]);

  const selected = sellers.find((seller) => seller.id === sellerId);
  const { data: articles = [], isLoading: articlesLoading } = useQuery({
    queryKey: ["wb-articles", sellerId],
    queryFn: () => getSellerArticles(sellerId),
    enabled: Boolean(sellerId),
  });
  const { data: history, isLoading: historyLoading } = useQuery({
    queryKey: ["wb-review-history", sellerId],
    queryFn: () => getSellerReviewHistory(sellerId),
    enabled: Boolean(sellerId),
  });
  const { data: reviewSync } = useQuery({
    queryKey: ["wb-review-sync"],
    queryFn: getLatestReviewSync,
    refetchInterval: (query) =>
      query.state.data?.status === "queued" || query.state.data?.status === "running" ? 3000 : false,
  });
  const products = history?.products ?? [];
  const subjects = useMemo(
    () => collectSubjects(products.length ? products : articles),
    [products, articles],
  );
  const latestDate = useMemo(() => latestCollectedDate(products), [products]);
  const visibleProducts = useMemo(
    () => sortByMovement(applyFilters(products, filters), latestDate),
    [products, filters, latestDate],
  );
  const visibleArticles = useMemo(() => applyFilters(articles, filters), [articles, filters]);
  const filtersActive = hasActiveFilters(filters);
  const processedSellers = reviewSync
    ? reviewSync.completed_sellers + reviewSync.failed_sellers
    : 0;
  const syncErrors = reviewSync?.jobs.filter((job) => job.status === "error") ?? [];
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["automation-sellers", AUTOMATION_ID] });
    await queryClient.invalidateQueries({ queryKey: ["wb-sellers"] });
  };
  // Only sellers the automation does not collect for yet can be connected.
  const { data: registry = [] } = useQuery({
    queryKey: ["wb-sellers", false],
    queryFn: () => getSellers(),
    enabled: connecting,
  });
  const saveMutation = useMutation({
    mutationFn: (payload: Partial<SellerInput>) => updateSeller((dialogSeller as Seller).id, payload),
    onSuccess: async (seller) => {
      setDialogSeller(null);
      setSellerId(seller.id);
      setFormError("");
      await refresh();
    },
    onError: (error) =>
      setFormError(error instanceof ApiError ? error.message : "Не удалось сохранить селлера"),
  });
  const connectMutation = useMutation({
    mutationFn: (payload: { seller_id: string } | SellerInput) => attachSeller(AUTOMATION_ID, payload),
    onSuccess: async (seller) => {
      setConnecting(false);
      setFormError("");
      setSellerId(seller.id);
      await refresh();
    },
    onError: (error) =>
      setFormError(error instanceof ApiError ? error.message : "Не удалось подключить селлера"),
  });
  const detachMutation = useMutation({
    mutationFn: (seller: Seller) => detachSeller(AUTOMATION_ID, seller.id),
    onSuccess: async () => {
      setDetaching(null);
      setSellerId("");
      await refresh();
    },
  });
  const retryMutation = useMutation({ mutationFn: retrySellerSync, onSuccess: refresh });
  const reviewSyncMutation = useMutation({
    mutationFn: startReviewSync,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["wb-review-sync"] });
    },
  });
  const reportMutation = useMutation({
    mutationFn: ({ dateFrom, dateTo }: { dateFrom: string; dateTo: string }) =>
      downloadReviewReport(sellerId, dateFrom, dateTo),
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename ?? "reviews.xlsx";
      link.click();
      URL.revokeObjectURL(url);
      setReportOpen(false);
      setReportError("");
    },
    onError: (error) =>
      setReportError(error instanceof ApiError ? error.message : "Не удалось сформировать отчёт"),
  });

  useEffect(() => {
    if (reviewSync?.finished_at) {
      void queryClient.invalidateQueries({ queryKey: ["wb-review-history"] });
      void queryClient.invalidateQueries({ queryKey: ["wb-articles"] });
    }
  }, [queryClient, reviewSync?.finished_at]);

  const openConnectDialog = () => {
    setFormError("");
    setConnecting(true);
  };

  return (
    <div className="app-page reviews-shell">
      <AppHeader current="Wildberries Reviews" />
      <div className="reviews-workspace">
        <SellerSidebar
          sellers={sellers}
          selectedId={sellerId}
          onSelect={setSellerId}
          onAdd={openConnectDialog}
          onEdit={(seller) => {
            setFormError("");
            setDialogSeller(seller);
          }}
          onDetach={setDetaching}
          onRetry={(seller) => retryMutation.mutate(seller.id)}
        />
        <main className="reviews-content">
          <div className="reviews-heading">
            <div>
              <p className="eyebrow">Wildberries / товары</p>
              <h1>Мониторинг отзывов</h1>
              <p className="muted">
                Каталог товаров и подготовка ежедневных снимков отзывов
                {selected ? ` для ${selected.name}` : ""}.
              </p>
            </div>
            <div className="review-sync-actions">
              {reviewSync && (
                <div className="last-sync">
                  <span className="eyebrow">Последний сбор</span>
                  <strong>
                    {reviewSync.status === "queued" && "В очереди"}
                    {reviewSync.status === "running" &&
                      `${processedSellers} из ${reviewSync.total_sellers}`}
                    {reviewSync.status === "success" && "Завершён"}
                    {reviewSync.status === "partial_success" && "Завершён с ошибками"}
                    {reviewSync.status === "error" && "Ошибка"}
                  </strong>
                </div>
              )}
              <button
                className="primary-button review-sync-button"
                disabled={
                  sellers.length === 0 ||
                  reviewSyncMutation.isPending ||
                  reviewSync?.status === "queued" ||
                  reviewSync?.status === "running"
                }
                onClick={() => reviewSyncMutation.mutate()}
              >
                {reviewSyncMutation.isPending ? "Запускаем…" : "Синхронизация отзывов"}
              </button>
              <button
                className="secondary-button"
                disabled={!selected}
                onClick={() => {
                  setReportError("");
                  setReportOpen(true);
                }}
              >
                Отчёт XLSX
              </button>
              {selected && (
                <div className="last-sync catalog-status">
                  <span className="eyebrow">Статус каталога</span>
                  <strong>{syncStatusText[selected.catalog_sync_status]}</strong>
                </div>
              )}
            </div>
          </div>
          {reviewSyncMutation.isError && (
            <div className="inline-error">
              {reviewSyncMutation.error instanceof ApiError
                ? reviewSyncMutation.error.message
                : "Не удалось запустить синхронизацию"}
            </div>
          )}
          {syncErrors.length > 0 && (
            <div className="inline-error sync-errors" role="status">
              <strong>Не удалось собрать данные:</strong>
              <ul>
                {syncErrors.map((job) => (
                  <li key={job.id}>
                    {job.seller_name}: {job.error ?? "неизвестная ошибка"}
                    {job.attempts > 1 && ` (попыток: ${job.attempts})`}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {isLoading || articlesLoading || historyLoading ? (
            <div className="loading-block">Загружаем данные…</div>
          ) : !selected ? (
            <div className="empty-state">
              <h2>Подключите первого селлера</h2>
              <p>
                Возьмите селлера из реестра или заведите нового — снимки отзывов начнут собираться
                со следующего прогона.
              </p>
              <button className="primary-button" onClick={openConnectDialog}>
                Подключить селлера
              </button>
            </div>
          ) : selected.catalog_sync_status === "queued" ||
            selected.catalog_sync_status === "syncing" ? (
            <div className="empty-state">
              <h2>Получаем товары</h2>
              <p>Синхронизация выполняется в фоне. Список обновится автоматически.</p>
            </div>
          ) : products.some((product) => product.snapshots.length) ? (
            <>
              <ArticleFilters
                filters={filters}
                subjects={subjects}
                shown={visibleProducts.length}
                total={products.length}
                onChange={setFilters}
              />
              {visibleProducts.length ? (
                <ReviewTimeline products={visibleProducts} latestDate={latestDate} />
              ) : (
                <NothingFound onReset={() => setFilters({ query: "", subjects: [] })} />
              )}
            </>
          ) : articles.length ? (
            <>
            <ArticleFilters
              filters={filters}
              subjects={subjects}
              shown={visibleArticles.length}
              total={articles.length}
              onChange={setFilters}
            />
            {visibleArticles.length === 0 ? (
              <NothingFound onReset={() => setFilters({ query: "", subjects: [] })} />
            ) : (
            <section className="article-catalog">
              <div className="article-head">
                <span>Товар</span>
                <span>Артикул WB</span>
                <span>Артикул продавца</span>
                <span>Статус</span>
              </div>
              {visibleArticles.map((article) => (
                <div className="article-row" key={article.id}>
                  <div className="article-title">
                    {article.photo_url ? (
                      <img className="product-photo" src={article.photo_url} alt="" loading="lazy" />
                    ) : (
                      <span className="product-photo product-photo-empty" aria-hidden="true" />
                    )}
                    <span>
                      <strong>{article.name}</strong>
                      {(article.brand || article.subject_name) && (
                        <em>{[article.brand, article.subject_name].filter(Boolean).join(" · ")}</em>
                      )}
                    </span>
                  </div>
                  <code>
                    <a
                      className="wb-article-link"
                      href={`https://www.wildberries.ru/catalog/${article.article}/detail.aspx`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {article.article}
                    </a>
                  </code>
                  <code>{article.vendor_code || "—"}</code>
                  <span className={`product-state product-state-${article.state}`}>
                    {articleStateText[article.state]}
                  </span>
                </div>
              ))}
            </section>
            )}
            </>
          ) : (
            <div className="empty-state">
              <h2>Товары не найдены</h2>
              <p>{selected.catalog_sync_error ?? "Wildberries не вернул активных карточек."}</p>
              {selected.catalog_sync_status === "error" && (
                <button className="primary-button" onClick={() => retryMutation.mutate(selected.id)}>
                  Повторить
                </button>
              )}
            </div>
          )}
        </main>
      </div>
      {reportOpen && selected && (
        <ReviewReportDialog
          sellerName={selected.name}
          pending={reportMutation.isPending}
          error={reportError}
          onClose={() => setReportOpen(false)}
          onSubmit={(dateFrom, dateTo) => reportMutation.mutate({ dateFrom, dateTo })}
        />
      )}
      {dialogSeller && (
        <SellerDialog
          seller={dialogSeller}
          pending={saveMutation.isPending}
          error={formError}
          onClose={() => setDialogSeller(null)}
          onSubmit={(value) => saveMutation.mutate(value)}
        />
      )}
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
          description={`«${detaching.name}» перестанет попадать в ежедневный сбор. Селлер останется в реестре, собранная история отзывов сохранится — подключить обратно можно в любой момент.`}
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
