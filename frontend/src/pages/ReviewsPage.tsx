import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { ApiError } from "../api/http";
import { AppHeader } from "../components/AppHeader";
import { ReviewTimeline } from "../components/ReviewTimeline";
import { DeleteSellerDialog, SellerDialog } from "../components/SellerDialog";
import { SellerSidebar } from "../components/SellerSidebar";
import {
  createSeller,
  deleteSeller,
  getSellerArticles,
  getSellerReviewHistory,
  getLatestReviewSync,
  getSellers,
  retrySellerSync,
  startReviewSync,
  updateSeller,
} from "../features/reviews/api";
import type { Seller, SellerInput } from "../features/reviews/types";

const syncStatusText: Record<Seller["catalog_sync_status"], string> = {
  queued: "В очереди",
  syncing: "Синхронизация",
  success: "Готово",
  error: "Ошибка",
};

export function ReviewsPage() {
  const queryClient = useQueryClient();
  const { data: sellers = [], isLoading } = useQuery({
    queryKey: ["wb-sellers"],
    queryFn: getSellers,
    refetchInterval: (query) =>
      query.state.data?.some(
        (item) => item.catalog_sync_status === "queued" || item.catalog_sync_status === "syncing",
      )
        ? 3000
        : false,
  });
  const [sellerId, setSellerId] = useState("");
  const [dialogSeller, setDialogSeller] = useState<Seller | "new" | null>(null);
  const [deleting, setDeleting] = useState<Seller | null>(null);
  const [formError, setFormError] = useState("");

  useEffect(() => {
    if (!sellers.some((item) => item.id === sellerId)) {
      setSellerId(sellers[0]?.id ?? "");
    }
  }, [sellerId, sellers]);

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
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["wb-sellers"] });
  const saveMutation = useMutation({
    mutationFn: (payload: SellerInput | Partial<SellerInput>) =>
      dialogSeller === "new"
        ? createSeller(payload as SellerInput)
        : updateSeller((dialogSeller as Seller).id, payload),
    onSuccess: async (seller) => {
      setDialogSeller(null);
      setSellerId(seller.id);
      setFormError("");
      await refresh();
    },
    onError: (error) =>
      setFormError(error instanceof ApiError ? error.message : "Не удалось сохранить селлера"),
  });
  const deleteMutation = useMutation({
    mutationFn: deleteSeller,
    onSuccess: async () => {
      setDeleting(null);
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

  useEffect(() => {
    if (reviewSync?.finished_at) {
      void queryClient.invalidateQueries({ queryKey: ["wb-review-history"] });
      void queryClient.invalidateQueries({ queryKey: ["wb-articles"] });
    }
  }, [queryClient, reviewSync?.finished_at]);

  const openNewSeller = () => {
    setFormError("");
    setDialogSeller("new");
  };

  return (
    <div className="app-page reviews-shell">
      <AppHeader current="Wildberries Reviews" />
      <div className="reviews-workspace">
        <SellerSidebar
          sellers={sellers}
          selectedId={sellerId}
          onSelect={setSellerId}
          onAdd={openNewSeller}
          onEdit={(seller) => {
            setFormError("");
            setDialogSeller(seller);
          }}
          onDelete={setDeleting}
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
                      `${reviewSync.completed_sellers} из ${reviewSync.total_sellers}`}
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
          {isLoading || articlesLoading || historyLoading ? (
            <div className="loading-block">Загружаем данные…</div>
          ) : !selected ? (
            <div className="empty-state">
              <h2>Добавьте первого селлера</h2>
              <p>После добавления система отправит задачу в Kafka и получит карточки Wildberries.</p>
              <button className="primary-button" onClick={openNewSeller}>
                Добавить селлера
              </button>
            </div>
          ) : selected.catalog_sync_status === "queued" ||
            selected.catalog_sync_status === "syncing" ? (
            <div className="empty-state">
              <h2>Получаем товары</h2>
              <p>Синхронизация выполняется в фоне. Список обновится автоматически.</p>
            </div>
          ) : history?.products.some((product) => product.snapshots.length) ? (
            <ReviewTimeline products={history.products} />
          ) : articles.length ? (
            <section className="article-catalog">
              <div className="article-head">
                <span>Товар</span>
                <span>Артикул WB</span>
                <span>Артикул продавца</span>
              </div>
              {articles.map((article) => (
                <div className="article-row" key={article.id}>
                  <strong>{article.name}</strong>
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
                </div>
              ))}
            </section>
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
      {dialogSeller && (
        <SellerDialog
          seller={dialogSeller === "new" ? undefined : dialogSeller}
          pending={saveMutation.isPending}
          error={formError}
          onClose={() => setDialogSeller(null)}
          onSubmit={(value) => saveMutation.mutate(value)}
        />
      )}
      {deleting && (
        <DeleteSellerDialog
          seller={deleting}
          pending={deleteMutation.isPending}
          onClose={() => setDeleting(null)}
          onConfirm={() => deleteMutation.mutate(deleting.id)}
        />
      )}
    </div>
  );
}
