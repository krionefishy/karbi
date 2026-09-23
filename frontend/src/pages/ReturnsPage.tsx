import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Link2, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError } from "../api/http";
import { ConnectSellerDialog } from "../components/ConnectSellerDialog";
import { ReturnsExtension } from "../components/ReturnsExtension";
import { ReturnsClaims, ReturnsTable } from "../components/ReturnsTable";
import { ConfirmDialog } from "../components/SellerDialog";
import { SellerSwitcher } from "../components/SellerSwitcher";
import { Shell } from "../components/Shell";
import { staleRefreshError } from "../features/refresh";
import { createInviteLink, getRefreshState, getReturns, requestRefresh } from "../features/returns/api";
import {
  attachSeller,
  detachSeller,
  getAutomationSellers,
  getSellers,
  retrySellerSync,
} from "../features/sellers/api";
import type { Seller, SellerInput } from "../features/sellers/types";

const AUTOMATION_ID = "wb-returns";
const AUTOMATION_TITLE = "Возвраты WB";

const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });

type Tab = "ready" | "transit" | "claims" | "history" | "extension";

export function ReturnsPage() {
  const queryClient = useQueryClient();
  const [sellerId, setSellerId] = useState("");
  const [tab, setTab] = useState<Tab>("ready");
  const [connecting, setConnecting] = useState(false);
  const [detaching, setDetaching] = useState<Seller | null>(null);
  const [formError, setFormError] = useState("");
  const [actionError, setActionError] = useState("");
  const [invite, setInvite] = useState("");
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
  const { data: returns, isLoading: returnsLoading } = useQuery({
    queryKey: ["wb-returns", sellerId],
    queryFn: () => getReturns(sellerId),
    enabled: Boolean(sellerId),
  });

  useEffect(() => {
    if (!sellers.some((item) => item.id === sellerId)) {
      setSellerId(sellers[0]?.id ?? "");
    }
  }, [sellerId, sellers]);
  useEffect(() => {
    setActionError("");
    setInvite("");
    setCopied(false);
  }, [sellerId]);

  const refreshSellers = async () => {
    await queryClient.invalidateQueries({ queryKey: ["automation-sellers", AUTOMATION_ID] });
    await queryClient.invalidateQueries({ queryKey: ["wb-sellers"] });
  };
  const invalidateReturns = () => queryClient.invalidateQueries({ queryKey: ["wb-returns", sellerId] });
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

  // Пока идёт сбор, состояние опрашивается; страница обновится сама, как только он закончится.
  const { data: refreshState } = useQuery({
    queryKey: ["wb-returns-refresh", sellerId],
    queryFn: () => getRefreshState(sellerId),
    enabled: Boolean(sellerId),
    refetchInterval: (query) => (query.state.data?.in_progress ? 3000 : false),
  });
  const refreshing = Boolean(refreshState?.in_progress);
  const refreshError = staleRefreshError(refreshState, returns?.collected_at);
  useEffect(() => {
    if (refreshState?.status === "success") void invalidateReturns();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshState?.status, refreshState?.finished_at, sellerId]);
  const refreshMutation = useMutation({
    mutationFn: () => requestRefresh(sellerId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["wb-returns-refresh", sellerId] }),
    onError: fail("Не удалось поставить обновление в очередь"),
  });
  const inviteMutation = useMutation({
    mutationFn: () => createInviteLink(sellerId),
    onSuccess: (link) => {
      setInvite(link.url);
      setCopied(false);
      setActionError("");
    },
    onError: fail("Не удалось выпустить ссылку"),
  });

  const selected = sellers.find((seller) => seller.id === sellerId);
  const counts = {
    ready: returns?.ready.length ?? 0,
    transit: (returns?.transit.length ?? 0) + (returns?.other_active.length ?? 0),
    claims: returns?.claims.length ?? 0,
    history: (returns?.history.length ?? 0) + (returns?.claims_history.length ?? 0),
    extension: null,
  };

  return (
    <Shell
      current={AUTOMATION_TITLE}
      seller={
        <SellerSwitcher
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
      }
    >
      <main className="reviews-content">
        <div className="reviews-heading">
          <div>
            <h1>{AUTOMATION_TITLE}</h1>
            <p className="muted">
              {selected
                ? `Возвраты ${selected.name} в ПВЗ и заявки покупателей: что готово к выдаче, что едет и сколько дней до платного хранения.`
                : "Возвраты товаров продавцу и заявки покупателей на возврат по кабинету."}
            </p>
          </div>
          {selected && (
            <div className="review-sync-actions">
              <div className="last-sync">
                <span className="eyebrow">Готово к выдаче</span>
                <strong>{counts.ready}</strong>
              </div>
              <div className="last-sync">
                <span className="eyebrow">Данные от</span>
                <strong>{returns?.collected_at ? momentFormatter.format(new Date(returns.collected_at)) : "—"}</strong>
              </div>
              <button
                className="secondary-button"
                disabled={refreshing || refreshMutation.isPending}
                onClick={() => refreshMutation.mutate()}
                title="Перечитать отчёт о возвратах и заявки прямо сейчас"
              >
                <RefreshCw size={15} className={refreshing ? "spinning" : undefined} />
                {refreshing ? "Обновляем…" : "Обновить данные"}
              </button>
              <button className="primary-button" disabled={inviteMutation.isPending} onClick={() => inviteMutation.mutate()}>
                <Link2 size={15} />
                {inviteMutation.isPending ? "Готовим…" : "Ссылка на бота"}
              </button>
            </div>
          )}
        </div>

        {actionError && <div className="inline-error">{actionError}</div>}
        {refreshError && <div className="inline-error">Обновление не удалось: {refreshError}</div>}
        {returns?.collection_error && <div className="checklist-notice">Последний сбор: {returns.collection_error}</div>}
        {invite && (
          <div className="invite-banner">
            <div>
              <span className="eyebrow">Ссылка для подписки на уведомления о возвратах</span>
              <code>{invite}</code>
              <em>Одноразовая: как только по ней подпишутся, она перестанет работать. Выпуск новой гасит эту.</em>
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

        {isLoading ? (
          <div className="loading-block">Загружаем данные…</div>
        ) : !selected ? (
          <div className="empty-state">
            <h2>Подключите первый кабинет</h2>
            <p>
              Возьмите селлера из реестра — возвраты за месяц и открытые заявки подтянутся в ближайшие минуты, а
              уведомления пойдут в Telegram по ссылке на бота.
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
        ) : (
          <>
            <div className="mode-switch" role="tablist" aria-label="Разделы возвратов">
              {(
                [
                  ["ready", "Готовы к выдаче"],
                  ["transit", "В пути"],
                  ["claims", "Заявки покупателей"],
                  ["history", "История"],
                  ["extension", "Расширение"],
                ] as [Tab, string][]
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  role="tab"
                  aria-selected={tab === key}
                  className={tab === key ? "mode-active" : undefined}
                  onClick={() => setTab(key)}
                >
                  {label}
                  {returns && counts[key] !== null && <small>{counts[key]}</small>}
                </button>
              ))}
            </div>
            {tab === "extension" ? (
              <ReturnsExtension sellerId={sellerId} />
            ) : returnsLoading || !returns ? (
              <div className="loading-block">Загружаем данные…</div>
            ) : tab === "ready" ? (
              <ReturnsTable rows={returns.ready} empty="Готовых к выдаче возвратов нет." storage />
            ) : tab === "transit" ? (
              <ReturnsTable
                rows={[...returns.transit, ...returns.other_active]}
                empty="Возвратов в пути нет."
              />
            ) : tab === "claims" ? (
              <ReturnsClaims claims={returns.claims} empty="Открытых заявок нет." />
            ) : (
              <>
                <ReturnsTable rows={returns.history} empty="Закрытых возвратов пока нет." />
                {returns.claims_history.length > 0 && (
                  <>
                    <h2 className="returns-subheading">Решённые заявки</h2>
                    <ReturnsClaims claims={returns.claims_history} empty="" />
                  </>
                )}
              </>
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
          description={`«${detaching.name}» перестанет собираться, уведомления по нему прекратятся. Собранное останется до повторного подключения.`}
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
