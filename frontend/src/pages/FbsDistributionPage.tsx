import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError } from "../api/http";
import { AppHeader } from "../components/AppHeader";
import { ConnectSellerDialog } from "../components/ConnectSellerDialog";
import { FbsMappingPanel } from "../components/FbsMappingPanel";
import { FbsPlanPanel } from "../components/FbsPlanPanel";
import { FbsSetupPanel } from "../components/FbsSetupPanel";
import { FbsStockPanel } from "../components/FbsStockPanel";
import { FbsWarehouseAdmin } from "../components/FbsWarehouseAdmin";
import { ConfirmDialog } from "../components/SellerDialog";
import { SellerSidebar } from "../components/SellerSidebar";
import { getQueue, getSellerOverview, setPlacement, setWriteEnabled, syncMirror } from "../features/fbs/api";
import { cargoLabel, warehouseState } from "../features/fbs/cargo";
import {
  attachSeller,
  detachSeller,
  getAutomationSellers,
  getSellers,
  retrySellerSync,
} from "../features/sellers/api";
import type { Seller, SellerInput } from "../features/sellers/types";

const AUTOMATION_ID = "wb-fbs-distribution";
const AUTOMATION_TITLE = "Распределение остатков FBS";

const dateFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "long" });
const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });

export function FbsDistributionPage() {
  const queryClient = useQueryClient();
  const [sellerId, setSellerId] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [detaching, setDetaching] = useState<Seller | null>(null);
  const [enablingWrite, setEnablingWrite] = useState(false);
  const [formError, setFormError] = useState("");
  const [syncError, setSyncError] = useState("");
  const [tab, setTab] = useState<"cabinet" | "setup" | "stock" | "mapping" | "plan">("cabinet");

  const { data: sellers = [], isLoading } = useQuery({
    queryKey: ["automation-sellers", AUTOMATION_ID],
    queryFn: () => getAutomationSellers(AUTOMATION_ID),
  });
  const { data: registry = [] } = useQuery({
    queryKey: ["wb-sellers", false],
    queryFn: () => getSellers(),
    enabled: connecting,
  });
  const { data: overview } = useQuery({
    queryKey: ["fbs-overview", sellerId],
    queryFn: () => getSellerOverview(sellerId),
    enabled: Boolean(sellerId),
  });
  const { data: queue = [] } = useQuery({
    queryKey: ["fbs-queue", sellerId],
    queryFn: () => getQueue(sellerId),
    enabled: Boolean(sellerId),
  });

  useEffect(() => {
    if (!sellers.some((item) => item.id === sellerId)) {
      setSellerId(sellers[0]?.id ?? "");
    }
  }, [sellerId, sellers]);

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
    onError: (error) => setFormError(error instanceof ApiError ? error.message : "Не удалось подключить селлера"),
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
  const syncMutation = useMutation({
    mutationFn: () => syncMirror(sellerId),
    onSuccess: async () => {
      setSyncError("");
      await queryClient.invalidateQueries({ queryKey: ["fbs-overview", sellerId] });
      await queryClient.invalidateQueries({ queryKey: ["fbs-queue", sellerId] });
    },
    onError: (error) =>
      setSyncError(error instanceof ApiError ? error.message : "Не удалось сверить склады с Wildberries"),
  });
  const placementMutation = useMutation({
    mutationFn: ({ warehouseId, participates, position }: { warehouseId: number; participates: boolean; position: number }) =>
      setPlacement(sellerId, warehouseId, { participates, position }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["fbs-overview", sellerId] });
      await queryClient.invalidateQueries({ queryKey: ["fbs-queue", sellerId] });
    },
  });
  const modeMutation = useMutation({
    mutationFn: (enabled: boolean) => setWriteEnabled(sellerId, enabled),
    onSuccess: async () => {
      setEnablingWrite(false);
      await queryClient.invalidateQueries({ queryKey: ["fbs-overview", sellerId] });
    },
  });

  const selected = sellers.find((seller) => seller.id === sellerId);
  const writing = Boolean(overview?.write_enabled);
  const warehouses = overview?.warehouses ?? [];

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
              <h1>{AUTOMATION_TITLE}</h1>
              <p className="muted">
                Делит физический остаток товара между виртуальными FBS-складами кабинета.
              </p>
            </div>
            <span className="mode-switch">
              <button className={tab === "cabinet" ? "mode-active" : ""} onClick={() => setTab("cabinet")}>
                Кабинет
              </button>
              <button className={tab === "setup" ? "mode-active" : ""} onClick={() => setTab("setup")}>
                Схема распределения
              </button>
              <button className={tab === "stock" ? "mode-active" : ""} onClick={() => setTab("stock")}>
                Остаток 1С
              </button>
              <button className={tab === "mapping" ? "mode-active" : ""} onClick={() => setTab("mapping")}>
                Сопоставление
              </button>
              <button className={tab === "plan" ? "mode-active" : ""} onClick={() => setTab("plan")}>
                Расчёт
              </button>
            </span>
          </div>

          {tab === "setup" && <FbsSetupPanel />}
          {tab === "stock" && <FbsStockPanel />}
          {tab === "mapping" && <FbsMappingPanel sellers={sellers} />}
          {tab === "plan" &&
            (selected ? (
              <FbsPlanPanel sellerId={selected.id} sellerName={selected.name} writeEnabled={writing} />
            ) : (
              <div className="loading-block">Выберите кабинет слева, чтобы посчитать план.</div>
            ))}

          {tab === "cabinet" && (isLoading ? (
            <div className="loading-block">Загружаем кабинеты…</div>
          ) : !selected ? (
            <div className="loading-block">Подключите кабинет, чтобы настроить распределение.</div>
          ) : (
            <section className="automation-meta" aria-label="Состояние кабинета">
              <div>
                <dt>Кабинет</dt>
                <dd>{selected.name}</dd>
              </div>
              <div>
                <dt>Режим</dt>
                <dd>
                  {writing ? "Запись в Wildberries разрешена" : "Только расчёт, без записи"}
                  <span className="automation-run-detail">
                    {writing
                      ? "Рассчитанные остатки уходят в кабинет."
                      : "План считается и показывается, но в Wildberries ничего не уходит."}
                  </span>
                </dd>
              </div>
              <div>
                <dt>Подключён</dt>
                <dd>{overview ? dateFormatter.format(new Date(overview.enrolled_at)) : "—"}</dd>
              </div>
              <div>
                <dt>Справочник WB</dt>
                <dd>
                  {overview ? `${overview.offices_known} объектов` : "—"}
                  <span className="automation-run-detail">
                    {overview?.warehouses_synced_at
                      ? `сверено ${momentFormatter.format(new Date(overview.warehouses_synced_at))}`
                      : "ещё не сверялось"}
                  </span>
                </dd>
              </div>
              <div>
                <dt>Управление</dt>
                <dd>
                  <button
                    className="primary-button"
                    disabled={modeMutation.isPending || !overview}
                    onClick={() => (writing ? modeMutation.mutate(false) : setEnablingWrite(true))}
                  >
                    {writing ? "Выключить запись" : "Разрешить запись"}
                  </button>
                </dd>
              </div>
            </section>
          ))}

          {tab === "cabinet" && selected && (
            <>
              <div className="reviews-heading">
                <div>
                  <h2>Виртуальные склады</h2>
                  <p className="muted">
                    {warehouses.length
                      ? `${warehouses.length} складов в кабинете.`
                      : "В кабинете пока нет виртуальных FBS-складов."}
                  </p>
                </div>
                <button
                  className="primary-button"
                  disabled={syncMutation.isPending}
                  onClick={() => syncMutation.mutate()}
                >
                  <RefreshCw size={16} />
                  {syncMutation.isPending ? "Сверяем…" : "Сверить с Wildberries"}
                </button>
              </div>
              {syncError && <div className="form-error">{syncError}</div>}
              <section className="fbs-table" aria-label="Виртуальные склады кабинета">
                <div className="fbs-head">
                  <span>Склад</span>
                  <span>Город</span>
                  <span>Направление</span>
                  <span>Груз</span>
                  <span>Место</span>
                  <span>Участвует</span>
                </div>
                {warehouses.map((warehouse) => (
                  <div className="fbs-row" key={warehouse.warehouse_id}>
                    <span>
                      {warehouse.name}
                      <em className="stock-split">
                        склад {warehouse.warehouse_id} · объект {warehouse.office_id} · {warehouseState(warehouse)}
                      </em>
                    </span>
                    <span>{warehouse.city || "—"}</span>
                    <span>{warehouse.region_code ?? "не размечено"}</span>
                    <span>{cargoLabel(warehouse.cargo_type)}</span>
                    <span>
                      <input
                        aria-label={`Место склада ${warehouse.warehouse_id} внутри направления`}
                        type="number"
                        min={0}
                        defaultValue={warehouse.position}
                        onBlur={(event) =>
                          placementMutation.mutate({
                            warehouseId: warehouse.warehouse_id,
                            participates: warehouse.participates,
                            position: Number(event.target.value),
                          })
                        }
                      />
                    </span>
                    <span>
                      <input
                        aria-label={`Склад ${warehouse.warehouse_id} участвует в распределении`}
                        type="checkbox"
                        checked={warehouse.participates}
                        onChange={(event) =>
                          placementMutation.mutate({
                            warehouseId: warehouse.warehouse_id,
                            participates: event.target.checked,
                            position: warehouse.position,
                          })
                        }
                      />
                    </span>
                  </div>
                ))}
              </section>

              <FbsWarehouseAdmin sellerId={selected.id} writeEnabled={writing} warehouses={warehouses} />

              {queue.length > 0 && (
                <>
                  <div className="reviews-heading">
                    <div>
                      <h2>Очередь распределения</h2>
                      <p className="muted">
                        В этом порядке расчёт берёт склады: по одному из каждого направления, затем второй круг.
                      </p>
                    </div>
                  </div>
                  <ol className="fbs-queue">
                    {queue.map((entry) => (
                      <li key={entry.warehouse_id}>
                        <b>{entry.place}</b> {entry.name}
                        <em className="stock-split">
                          {entry.region_title}
                          {entry.city && ` · ${entry.city}`}
                        </em>
                      </li>
                    ))}
                  </ol>
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
      {enablingWrite && (
        <ConfirmDialog
          title="Разрешить запись в Wildberries?"
          description={`Автоматизация начнёт переписывать остатки на FBS-складах кабинета «${selected?.name ?? ""}». До этого она только считала план.`}
          confirmLabel="Разрешить"
          pendingLabel="Включаем…"
          pending={modeMutation.isPending}
          onClose={() => setEnablingWrite(false)}
          onConfirm={() => modeMutation.mutate(true)}
        />
      )}
      {detaching && (
        <ConfirmDialog
          title="Отключить от автоматизации?"
          description={`«${detaching.name}» перестанет участвовать в распределении остатков FBS. Остатки, уже опубликованные в Wildberries, останутся как есть.`}
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
