import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, KeyRound, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api/http";
import { AppHeader } from "../components/AppHeader";
import { ConfirmDialog, RestoreSellerDialog, SellerDialog } from "../components/SellerDialog";
import { getAutomations } from "../features/automations/api";
import {
  archiveSeller,
  createSeller,
  getSellers,
  purgeSeller,
  restoreSeller,
  retrySellerSync,
  updateSeller,
  verifySellerEgress,
} from "../features/sellers/api";
import type { Seller, SellerInput } from "../features/sellers/types";

const syncStatusText: Record<Seller["catalog_sync_status"], string> = {
  queued: "В очереди",
  syncing: "Синхронизация",
  success: "Готово",
  error: "Ошибка",
};

const egressStatusText: Record<string, string> = {
  verified: "проверен",
  delivered: "доставлен",
  key_invalid: "ключ отклонён",
  no_free_ip: "нет свободного IP",
  disabled: "отключён",
  undelivered: "не доставлен",
  unsynced: "не синхронизирован",
};

const dateFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });

export function SellersPage() {
  const queryClient = useQueryClient();
  const [showArchived, setShowArchived] = useState(false);
  const [dialogSeller, setDialogSeller] = useState<Seller | "new" | null>(null);
  const [archiving, setArchiving] = useState<Seller | null>(null);
  const [purging, setPurging] = useState<Seller | null>(null);
  const [restoring, setRestoring] = useState<Seller | null>(null);
  const [formError, setFormError] = useState("");

  const { data: sellers = [], isLoading } = useQuery({
    queryKey: ["wb-sellers", showArchived],
    queryFn: () => getSellers(showArchived),
    refetchInterval: (query) =>
      query.state.data?.some(
        (item) =>
          item.archived_at === null &&
          (item.catalog_sync_status === "queued" || item.catalog_sync_status === "syncing"),
      )
        ? 3000
        : false,
  });
  const { data: automations = [] } = useQuery({ queryKey: ["automations"], queryFn: getAutomations });
  const automationTitles = new Map(automations.map((item) => [item.id, item.title]));

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["wb-sellers"] });
  const saveMutation = useMutation({
    mutationFn: (payload: SellerInput | Partial<SellerInput>) =>
      dialogSeller === "new"
        ? createSeller(payload as SellerInput)
        : updateSeller((dialogSeller as Seller).id, payload),
    onSuccess: async () => {
      setDialogSeller(null);
      setFormError("");
      await refresh();
    },
    onError: (error) =>
      setFormError(error instanceof ApiError ? error.message : "Не удалось сохранить селлера"),
  });
  const archiveMutation = useMutation({
    mutationFn: archiveSeller,
    onSuccess: async () => {
      setArchiving(null);
      await refresh();
    },
  });
  const purgeMutation = useMutation({
    mutationFn: purgeSeller,
    onSuccess: async () => {
      setPurging(null);
      await refresh();
    },
  });
  const restoreMutation = useMutation({
    mutationFn: (apiKey: string) => restoreSeller((restoring as Seller).id, apiKey),
    onSuccess: async () => {
      setRestoring(null);
      setFormError("");
      await refresh();
    },
    onError: (error) =>
      setFormError(error instanceof ApiError ? error.message : "Не удалось восстановить селлера"),
  });
  const retryMutation = useMutation({ mutationFn: retrySellerSync, onSuccess: refresh });
  const egressVerifyMutation = useMutation({
    mutationFn: verifySellerEgress,
    onSuccess: refresh,
  });

  return (
    <div className="app-page">
      <AppHeader />
      <main className="page-container sellers-page">
        <div className="page-heading">
          <div>
            <p className="eyebrow">Рабочее пространство</p>
            <h1>Селлеры</h1>
            <p className="muted">
              Общий реестр. Отсюда селлера заводят и убирают, а автоматизации только подключают его
              к себе.
            </p>
          </div>
          <div className="registry-actions">
            <label className="archived-toggle">
              <input
                type="checkbox"
                checked={showArchived}
                onChange={(event) => setShowArchived(event.target.checked)}
              />
              Показывать архивных
            </label>
            <button
              className="primary-button"
              onClick={() => {
                setFormError("");
                setDialogSeller("new");
              }}
            >
              <Plus size={15} />
              Добавить селлера
            </button>
          </div>
        </div>

        {isLoading ? (
          <div className="loading-block">Загружаем реестр…</div>
        ) : sellers.length === 0 ? (
          <div className="empty-state">
            <h2>Реестр пуст</h2>
            <p>Заведите первого селлера — подключить его к автоматизациям можно будет сразу после.</p>
            <button
              className="primary-button"
              onClick={() => {
                setFormError("");
                setDialogSeller("new");
              }}
            >
              Добавить селлера
            </button>
          </div>
        ) : (
          <section className="registry-table">
            <div className="registry-head">
              <span>Селлер</span>
              <span>Товаров</span>
              <span>Каталог</span>
              <span>Ключ</span>
              <span>Автоматизации</span>
              <span>Действия</span>
            </div>
            {sellers.map((seller) => (
              <div className={`registry-row${seller.archived_at ? " registry-archived" : ""}`} key={seller.id}>
                <div className="registry-name">
                  <strong>{seller.name}</strong>
                  {seller.archived_at && (
                    <em>В архиве с {dateFormatter.format(new Date(seller.archived_at))}</em>
                  )}
                  {!seller.archived_at && seller.catalog_sync_error && (
                    <em className="registry-error">{seller.catalog_sync_error}</em>
                  )}
                </div>
                <span>{seller.product_count}</span>
                <span className={`catalog-state catalog-state-${seller.catalog_sync_status}`}>
                  {seller.archived_at ? "—" : syncStatusText[seller.catalog_sync_status]}
                </span>
                <span
                  className={`egress-state egress-state-${seller.egress_status}`}
                  title={seller.egress_error ?? undefined}
                >
                  {seller.archived_at ? "—" : (egressStatusText[seller.egress_status] ?? seller.egress_status)}
                </span>
                <span className="registry-automations">
                  {seller.automations.length === 0
                    ? "—"
                    : seller.automations.map((id) => (
                        <span className="automation-badge" key={id}>
                          {automationTitles.get(id) ?? id}
                        </span>
                      ))}
                </span>
                <span className="registry-row-actions">
                  {seller.archived_at ? (
                    <>
                      <button
                        title="Восстановить"
                        aria-label={`Восстановить ${seller.name}`}
                        onClick={() => {
                          setFormError("");
                          setRestoring(seller);
                        }}
                      >
                        <ArchiveRestore size={15} />
                      </button>
                      <button
                        className="danger-icon"
                        title="Удалить навсегда"
                        aria-label={`Удалить навсегда ${seller.name}`}
                        onClick={() => setPurging(seller)}
                      >
                        <Trash2 size={15} />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        title="Редактировать"
                        aria-label={`Редактировать ${seller.name}`}
                        onClick={() => {
                          setFormError("");
                          setDialogSeller(seller);
                        }}
                      >
                        <Pencil size={15} />
                      </button>
                      <button
                        title="Пересобрать каталог"
                        aria-label={`Пересобрать каталог ${seller.name}`}
                        onClick={() => retryMutation.mutate(seller.id)}
                      >
                        <RefreshCw size={15} />
                      </button>
                      {seller.egress_status !== "verified" && (
                        <button
                          title="Перепроверить ключ на шлюзе"
                          aria-label={`Перепроверить ключ ${seller.name}`}
                          onClick={() => egressVerifyMutation.mutate(seller.id)}
                        >
                          <KeyRound size={15} />
                        </button>
                      )}
                      <button
                        title="В архив"
                        aria-label={`Отправить в архив ${seller.name}`}
                        onClick={() => setArchiving(seller)}
                      >
                        <Archive size={15} />
                      </button>
                    </>
                  )}
                </span>
              </div>
            ))}
          </section>
        )}
      </main>

      {dialogSeller && (
        <SellerDialog
          seller={dialogSeller === "new" ? undefined : dialogSeller}
          pending={saveMutation.isPending}
          error={formError}
          onClose={() => setDialogSeller(null)}
          onSubmit={(value) => saveMutation.mutate(value)}
        />
      )}
      {archiving && (
        <ConfirmDialog
          title="Отправить в архив?"
          description={`«${archiving.name}» отключится от всех автоматизаций и перестанет собирать данные. Ключ будет удалён, собранная история останется. Вернуть можно в любой момент.`}
          confirmLabel="В архив"
          pendingLabel="Архивируем…"
          pending={archiveMutation.isPending}
          onClose={() => setArchiving(null)}
          onConfirm={() => archiveMutation.mutate(archiving.id)}
        />
      )}
      {purging && (
        <ConfirmDialog
          title="Удалить навсегда?"
          description={`«${purging.name}», его товары и вся собранная автоматизациями история будут удалены без возможности восстановления.`}
          confirmLabel="Удалить навсегда"
          pendingLabel="Удаляем…"
          pending={purgeMutation.isPending}
          danger
          onClose={() => setPurging(null)}
          onConfirm={() => purgeMutation.mutate(purging.id)}
        />
      )}
      {restoring && (
        <RestoreSellerDialog
          seller={restoring}
          pending={restoreMutation.isPending}
          error={formError}
          onClose={() => setRestoring(null)}
          onSubmit={(apiKey) => restoreMutation.mutate(apiKey)}
        />
      )}
    </div>
  );
}
