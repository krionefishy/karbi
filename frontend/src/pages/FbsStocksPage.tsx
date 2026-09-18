import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError } from "../api/http";
import { ConnectSellerDialog } from "../components/ConnectSellerDialog";
import { FbsStocksBoard } from "../components/FbsStocksBoard";
import { FbsStocksComparison } from "../components/FbsStocksComparison";
import { FbsStocksSetup } from "../components/FbsStocksSetup";
import { ConfirmDialog } from "../components/SellerDialog";
import { SellerSwitcher } from "../components/SellerSwitcher";
import { Shell } from "../components/Shell";
import {
  addBarcodes,
  downloadBoard,
  getBoard,
  getRefreshState,
  removeBarcode,
  setHidden,
  requestRefresh,
  requestRefreshAll,
  setNote,
} from "../features/fbsStocks/api";
import {
  attachSeller,
  detachSeller,
  getAutomationSellers,
  getSellers,
  retrySellerSync,
} from "../features/sellers/api";
import type { Seller, SellerInput } from "../features/sellers/types";
import { staleRefreshError } from "../features/refresh";

const AUTOMATION_ID = "wb-fbs-stocks";
const AUTOMATION_TITLE = "Остатки FBS по складам";

const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });

type Tab = "board" | "comparison" | "fulfilment" | "setup";

export function FbsStocksPage() {
  const queryClient = useQueryClient();
  const [sellerId, setSellerId] = useState("");
  const [tab, setTab] = useState<Tab>("board");
  const comparing = tab === "comparison" || tab === "fulfilment";
  const [connecting, setConnecting] = useState(false);
  const [detaching, setDetaching] = useState<Seller | null>(null);
  const [formError, setFormError] = useState("");
  const [actionError, setActionError] = useState("");
  const boardKey = ["fbs-stocks", sellerId];

  const { data: sellers = [], isLoading } = useQuery({
    queryKey: ["automation-sellers", AUTOMATION_ID],
    queryFn: () => getAutomationSellers(AUTOMATION_ID),
  });
  const { data: registry = [] } = useQuery({
    queryKey: ["wb-sellers", false],
    queryFn: () => getSellers(),
    enabled: connecting,
  });
  const { data: board, isLoading: boardLoading } = useQuery({
    queryKey: boardKey,
    queryFn: () => getBoard(sellerId),
    enabled: Boolean(sellerId),
  });

  useEffect(() => {
    if (!sellers.some((item) => item.id === sellerId)) {
      setSellerId(sellers[0]?.id ?? "");
    }
  }, [sellerId, sellers]);
  useEffect(() => setActionError(""), [sellerId, tab]);

  const refreshSellers = async () => {
    await queryClient.invalidateQueries({ queryKey: ["automation-sellers", AUTOMATION_ID] });
    await queryClient.invalidateQueries({ queryKey: ["wb-sellers"] });
  };
  const invalidateBoards = async () => {
    await queryClient.invalidateQueries({ queryKey: boardKey });
    await queryClient.invalidateQueries({ queryKey: ["fbs-stocks-boards"] });
  };
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

  // Пока идёт сбор, состояние опрашивается; таблица обновится сама, как только он закончится.
  const { data: refreshState } = useQuery({
    queryKey: ["fbs-stocks-refresh", sellerId],
    queryFn: () => getRefreshState(sellerId),
    enabled: Boolean(sellerId),
    refetchInterval: (query) => (query.state.data?.in_progress ? 3000 : false),
  });
  const refreshing = Boolean(refreshState?.in_progress);
  const refreshError = staleRefreshError(refreshState, board?.collected_at);
  useEffect(() => {
    if (refreshState?.status === "success") void invalidateBoards();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshState?.status, refreshState?.finished_at, sellerId]);
  const refreshMutation = useMutation({
    mutationFn: () => requestRefresh(sellerId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["fbs-stocks-refresh", sellerId] }),
    onError: fail("Не удалось поставить обновление в очередь"),
  });
  const refreshAllMutation = useMutation({
    mutationFn: requestRefreshAll,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["fbs-stocks-refresh"] }),
    onError: fail("Не удалось поставить обновление в очередь"),
  });
  const exportMutation = useMutation({
    mutationFn: downloadBoard,
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename ?? "fbs_stocks.xlsx";
      link.click();
      URL.revokeObjectURL(url);
      setActionError("");
    },
    onError: fail("Не удалось выгрузить таблицу"),
  });
  const noteMutation = useMutation({
    mutationFn: ({ barcode, note }: { barcode: string; note: string }) => setNote(sellerId, barcode, note),
    onError: fail("Не удалось сохранить заметку"),
    onSettled: invalidateBoards,
  });
  const hideMutation = useMutation({
    mutationFn: ({ barcode, hidden }: { barcode: string; hidden: boolean }) => setHidden(sellerId, barcode, hidden),
    onError: fail("Не удалось изменить видимость баркода"),
    onSettled: invalidateBoards,
  });
  const removeMutation = useMutation({
    mutationFn: (barcode: string) => removeBarcode(sellerId, barcode),
    onError: fail("Не удалось удалить баркод"),
    onSettled: invalidateBoards,
  });
  const addMutation = useMutation({
    mutationFn: (text: string) => addBarcodes(sellerId, text),
    onSuccess: async ({ added }) => {
      setActionError("");
      await invalidateBoards();
      // Остаток по новым строкам появится только после опроса WB — ставим его сразу.
      if (added > 0) refreshMutation.mutate();
    },
    onError: fail("Не удалось добавить баркоды"),
  });

  const selected = sellers.find((seller) => seller.id === sellerId);

  return (
    <Shell
      current={AUTOMATION_TITLE}
      seller={
        <SellerSwitcher
          sellers={sellers}
          selectedId={sellerId}
          onSelect={(id) => {
            setSellerId(id);
            if (comparing) setTab("board");
          }}
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
              <h1>
                {tab === "comparison"
                  ? "Сравнение по кабинетам"
                  : tab === "fulfilment"
                    ? "Сравнение ФФ"
                    : AUTOMATION_TITLE}
              </h1>
              <p className="muted">
                {tab === "comparison"
                  ? "Свои склады по отдельности, фулфилмент и округа — суммой. Все кабинеты на одном листе."
                  : tab === "fulfilment"
                    ? "Склады фулфилмента по отдельности, свои склады и округа — суммой. Все кабинеты на одном листе."
                    : selected
                    ? `Остатки ${selected.name} по складам кабинета WB. Нули подсвечены.`
                    : "Остатки по баркодам на складах кабинета WB."}
              </p>
            </div>
            <div className="review-sync-actions">
              <span className="mode-switch">
                <button className={tab === "board" ? "mode-active" : ""} onClick={() => setTab("board")}>
                  Таблица
                </button>
                <button className={tab === "comparison" ? "mode-active" : ""} onClick={() => setTab("comparison")}>
                  Сравнение
                </button>
                <button className={tab === "fulfilment" ? "mode-active" : ""} onClick={() => setTab("fulfilment")}>
                  Сравнение ФФ
                </button>
                <button className={tab === "setup" ? "mode-active" : ""} onClick={() => setTab("setup")} disabled={!selected}>
                  Столбцы
                </button>
              </span>
              {selected && !comparing && (
                <>
                  <div className="last-sync">
                    <span className="eyebrow">Остатки от</span>
                    <strong>{board?.collected_at ? momentFormatter.format(new Date(board.collected_at)) : "—"}</strong>
                  </div>
                  <button
                    className="secondary-button"
                    disabled={refreshing || refreshMutation.isPending}
                    onClick={() => refreshMutation.mutate()}
                    title="Спросить остатки у WB прямо сейчас"
                  >
                    <RefreshCw size={15} className={refreshing ? "spinning" : undefined} />
                    {refreshing ? "Обновляем…" : "Обновить данные"}
                  </button>
                </>
              )}
              {comparing && (
                <button
                  className="secondary-button"
                  disabled={refreshAllMutation.isPending}
                  onClick={() => refreshAllMutation.mutate()}
                  title="Поставить в очередь все кабинеты"
                >
                  <RefreshCw size={15} />
                  Обновить все кабинеты
                </button>
              )}
              <button
                className="primary-button"
                disabled={exportMutation.isPending || sellers.length === 0}
                onClick={() => exportMutation.mutate()}
                title="Книга: лист на кабинет, «Сравнение» и «Сравнение ФФ»"
              >
                <Download size={15} />
                {exportMutation.isPending ? "Собираем…" : "Выгрузить в Excel"}
              </button>
            </div>
          </div>

          {actionError && <div className="inline-error">{actionError}</div>}
          {!comparing && refreshError && <div className="inline-error">Обновление не удалось: {refreshError}</div>}
          {!comparing && board?.collection_error && (
            <div className="checklist-notice">Последний сбор: {board.collection_error}</div>
          )}

          {comparing ? (
            <FbsStocksComparison expand={tab === "fulfilment" ? "fulfilment" : "own"} />
          ) : isLoading || (sellerId && boardLoading) ? (
            <div className="loading-block">Загружаем данные…</div>
          ) : !selected ? (
            <div className="empty-state">
              <h2>Подключите первый кабинет</h2>
              <p>
                Возьмите селлера из реестра, разложите склады кабинета по группам во вкладке «Столбцы» и впишите
                баркоды — остатки подтянутся сами.
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
          ) : tab === "setup" ? (
            <FbsStocksSetup sellerId={sellerId} />
          ) : board ? (
            <FbsStocksBoard
              board={board}
              onNote={(barcode, note) => noteMutation.mutate({ barcode, note })}
              onHide={(barcode, hidden) => hideMutation.mutate({ barcode, hidden })}
              onRemove={(barcode) => removeMutation.mutate(barcode)}
              onAdd={(text) => addMutation.mutate(text)}
              adding={addMutation.isPending}
            />
          ) : null}
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
          description={`«${detaching.name}» перестанет опрашиваться. Группы, баркоды и заметки останутся до повторного подключения.`}
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
