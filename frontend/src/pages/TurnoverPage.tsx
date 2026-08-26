import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Link2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError } from "../api/http";
import { AppHeader } from "../components/AppHeader";
import { ConnectSellerDialog } from "../components/ConnectSellerDialog";
import { ConfirmDialog } from "../components/SellerDialog";
import { SellerSidebar } from "../components/SellerSidebar";
import {
  attachSeller,
  detachSeller,
  getAutomationSellers,
  getSellers,
  retrySellerSync,
} from "../features/sellers/api";
import type { Seller, SellerInput } from "../features/sellers/types";
import {
  createInviteLink,
  getRefreshState,
  getTurnoverArticles,
  requestRefresh,
} from "../features/turnover/api";
import { belowThreshold, coverLabel } from "../features/turnover/cover";
import type { TurnoverStatus } from "../features/turnover/types";

const AUTOMATION_ID = "wb-turnover";
const AUTOMATION_TITLE = "Оборачиваемость товаров Wildberries";

const statusText: Record<TurnoverStatus, string> = {
  ok: "Считается",
  no_stock: "Нет остатка",
  no_sales: "Нет заказов",
  insufficient_data: "Мало данных",
};

const dateFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "long" });

export function TurnoverPage() {
  const queryClient = useQueryClient();
  const [sellerId, setSellerId] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [detaching, setDetaching] = useState<Seller | null>(null);
  const [formError, setFormError] = useState("");
  const [invite, setInvite] = useState<string>("");
  const [copied, setCopied] = useState(false);

  const { data: sellers = [], isLoading } = useQuery({
    queryKey: ["automation-sellers", AUTOMATION_ID],
    queryFn: () => getAutomationSellers(AUTOMATION_ID),
  });
  const { data: registry = [] } = useQuery({
    queryKey: ["wb-sellers", false],
    queryFn: () => getSellers(),
    enabled: connecting,
  });
  const { data: turnover, isLoading: turnoverLoading } = useQuery({
    queryKey: ["turnover-articles", sellerId],
    queryFn: () => getTurnoverArticles(sellerId),
    enabled: Boolean(sellerId),
  });

  useEffect(() => {
    if (!sellers.some((item) => item.id === sellerId)) {
      setSellerId(sellers[0]?.id ?? "");
    }
  }, [sellerId, sellers]);
  useEffect(() => {
    setInvite("");
    setCopied(false);
  }, [sellerId]);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["automation-sellers", AUTOMATION_ID] });
    await queryClient.invalidateQueries({ queryKey: ["wb-sellers"] });
  };
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
  // While a collection is running the state is polled; the table refreshes by
  // itself the moment it finishes, so nobody has to guess when to reload.
  const { data: refreshState } = useQuery({
    queryKey: ["turnover-refresh", sellerId],
    queryFn: () => getRefreshState(sellerId),
    enabled: Boolean(sellerId),
    refetchInterval: (query) => (query.state.data?.in_progress ? 3000 : false),
  });
  const refreshing = Boolean(refreshState?.in_progress);
  useEffect(() => {
    if (refreshState?.status === "success") {
      void queryClient.invalidateQueries({ queryKey: ["turnover-articles", sellerId] });
    }
  }, [queryClient, refreshState?.status, refreshState?.finished_at, sellerId]);
  const refreshMutation = useMutation({
    mutationFn: () => requestRefresh(sellerId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["turnover-refresh", sellerId] });
    },
  });
  const inviteMutation = useMutation({
    mutationFn: () => createInviteLink(sellerId),
    onSuccess: (link) => {
      setInvite(link.url);
      setCopied(false);
    },
  });

  const selected = sellers.find((seller) => seller.id === sellerId);
  const threshold = turnover?.threshold_days ?? 10;
  const articles = turnover?.articles ?? [];
  const alerting = belowThreshold(articles, threshold);

  return (
    <div className="app-page reviews-shell">
      <AppHeader current="Оборачиваемость" />
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
              <p className="eyebrow">Wildberries / запасы</p>
              <h1>Оборачиваемость</h1>
              <p className="muted">
                На сколько дней хватит текущего остатка при нынешнем темпе заказов
                {selected ? ` у ${selected.name}` : ""}. Уведомление уходит в Telegram, когда
                остаётся меньше {threshold} дней.
              </p>
            </div>
            {selected && (
              <div className="review-sync-actions">
                <div className="last-sync">
                  <span className="eyebrow">Ниже порога</span>
                  <strong>{alerting.length}</strong>
                </div>
                <div className="last-sync">
                  <span className="eyebrow">Расчёт от</span>
                  <strong>{turnover?.date ? dateFormatter.format(new Date(turnover.date)) : "—"}</strong>
                </div>
                <button
                  className="secondary-button"
                  disabled={refreshing || refreshMutation.isPending}
                  onClick={() => refreshMutation.mutate()}
                  title="Собрать остатки и заказы прямо сейчас"
                >
                  <RefreshCw size={15} className={refreshing ? "spinning" : undefined} />
                  {refreshing ? "Обновляем…" : "Обновить данные"}
                </button>
                <button
                  className="primary-button"
                  disabled={inviteMutation.isPending}
                  onClick={() => inviteMutation.mutate()}
                >
                  <Link2 size={15} />
                  {inviteMutation.isPending ? "Готовим…" : "Ссылка на бота"}
                </button>
              </div>
            )}
          </div>

          {refreshState?.status === "error" && !refreshing && (
            <div className="inline-error">Обновление не удалось: {refreshState.error ?? "неизвестная ошибка"}</div>
          )}
          {inviteMutation.isError && (
            <div className="inline-error">
              {inviteMutation.error instanceof ApiError
                ? inviteMutation.error.message
                : "Не удалось выпустить ссылку"}
            </div>
          )}
          {invite && (
            <div className="invite-banner">
              <div>
                <span className="eyebrow">Ссылка для подписки на уведомления</span>
                <code>{invite}</code>
                <em>
                  Одноразовая: как только по ней подпишутся, она перестанет работать. Выпуск новой
                  гасит эту.
                </em>
              </div>
              <button
                className="secondary-button"
                onClick={() => {
                  void navigator.clipboard?.writeText(invite);
                  setCopied(true);
                }}
              >
                <Copy size={15} />
                {copied ? "Скопировано" : "Копировать"}
              </button>
            </div>
          )}

          {isLoading || turnoverLoading ? (
            <div className="loading-block">Загружаем данные…</div>
          ) : !selected ? (
            <div className="empty-state">
              <h2>Подключите первого селлера</h2>
              <p>
                Возьмите селлера из реестра или заведите нового — остатки и заказы начнут собираться
                в ближайший слот, а первый расчёт появится следующей ночью.
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
          ) : articles.length === 0 ? (
            <div className="empty-state">
              <h2>Данных пока нет</h2>
              <p>
                Первый снимок остатков будет взят в ближайший слот сбора, заказы подтянутся за две
                недели назад. Метрика появится после ночного расчёта.
              </p>
            </div>
          ) : (
            <section className="turnover-table">
              <div className="turnover-head">
                <span>Товар</span>
                <span>Хватит на</span>
                <span>Остаток</span>
                <span>Заказов за 14 дн.</span>
                <span>В среднем в день</span>
                <span>Оборачиваемость</span>
                <span>Статус</span>
              </div>
              {articles.map((item) => {
                const cover = coverLabel(item, threshold);
                return (
                  <div className="turnover-row" key={item.article}>
                    <div className="article-title">
                      {item.photo_url ? (
                        <img className="product-photo" src={item.photo_url} alt="" loading="lazy" />
                      ) : (
                        <span className="product-photo product-photo-empty" aria-hidden="true" />
                      )}
                      <span>
                        <strong>{item.name || "Без названия"}</strong>
                        <em>
                          <a
                            className="wb-article-link"
                            href={`https://www.wildberries.ru/catalog/${item.article}/detail.aspx`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {item.article}
                          </a>
                        </em>
                      </span>
                    </div>
                    <span className={`cover cover-${cover.tone}`}>{cover.text}</span>
                    <span>
                      {item.stock_total}
                      {item.stock_fbs > 0 && (
                        <em className="stock-split">
                          WB {item.stock_fbo} · свой {item.stock_fbs}
                        </em>
                      )}
                    </span>
                    <span>{item.orders_count}</span>
                    <span>{item.avg_daily_orders.toFixed(2)}</span>
                    <span>
                      {item.turnover_days === null ? "—" : `${item.turnover_days.toFixed(1)} дн.`}
                      {item.stock_days > 0 && item.stock_days < 14 && (
                        <em className="stock-split">по {item.stock_days} дн. из 14</em>
                      )}
                    </span>
                    <span className={`turnover-state turnover-state-${item.status}`}>
                      {statusText[item.status]}
                    </span>
                  </div>
                );
              })}
            </section>
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
          description={`«${detaching.name}» перестанет попадать в сбор остатков и заказов, уведомления по нему приходить не будут. Селлер и собранные данные останутся.`}
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
