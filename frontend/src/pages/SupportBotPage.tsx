import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pause, Play } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError } from "../api/http";
import { ConnectSellerDialog } from "../components/ConnectSellerDialog";
import { ConfirmDialog } from "../components/SellerDialog";
import { SellerSwitcher } from "../components/SellerSwitcher";
import { SupportBotPace } from "../components/SupportBotPace";
import { SupportBotMonitor } from "../components/SupportBotMonitor";
import { SupportBotProducts } from "../components/SupportBotProducts";
import { SupportBotReplies } from "../components/SupportBotReplies";
import { SupportBotJournal } from "../components/SupportBotJournal";
import { SupportBotTemplates } from "../components/SupportBotTemplates";
import { Shell } from "../components/Shell";
import {
  addTemplate,
  deleteTemplate,
  getSends,
  getSupportBot,
  savePace,
  setMode,
  setPaused,
  setTemplateActive,
  updateTemplate,
} from "../features/supportBot/api";
import { MODE_LABEL, STATE_LABEL, STATE_ORDER, statusLine, totalDialogs } from "../features/supportBot/format";
import type { BotMode, JournalFilter, PaceInput } from "../features/supportBot/types";
import {
  attachSeller,
  detachSeller,
  getAutomationSellers,
  getSellers,
  retrySellerSync,
} from "../features/sellers/api";
import type { Seller, SellerInput } from "../features/sellers/types";

const AUTOMATION_ID = "wb-support-bot";
const AUTOMATION_TITLE = "Бот поддержки в чатах";

function message(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

export function SupportBotPage() {
  const queryClient = useQueryClient();
  const [sellerId, setSellerId] = useState("");
  const [journalFilter, setJournalFilter] = useState<JournalFilter>({ status: "", page: 1 });
  const [connecting, setConnecting] = useState(false);
  const [detaching, setDetaching] = useState<Seller | null>(null);
  const [pendingMode, setPendingMode] = useState<"live" | null>(null);
  const [tab, setTab] = useState<"dialogs" | "replies" | "monitor">("dialogs");
  const [formError, setFormError] = useState("");
  const [actionError, setActionError] = useState("");
  const [paceError, setPaceError] = useState("");
  const [paceSaved, setPaceSaved] = useState<number | null>(null);

  const { data: sellers = [], isLoading } = useQuery({
    queryKey: ["automation-sellers", AUTOMATION_ID],
    queryFn: () => getAutomationSellers(AUTOMATION_ID),
  });
  const { data: registry = [] } = useQuery({
    queryKey: ["wb-sellers", false],
    queryFn: () => getSellers(),
    enabled: connecting,
  });
  const bot = useQuery({
    queryKey: ["support-bot", sellerId],
    queryFn: () => getSupportBot(sellerId),
    enabled: Boolean(sellerId),
  });
  const sends = useQuery({
    queryKey: ["support-bot-sends", sellerId, journalFilter],
    queryFn: () => getSends(sellerId, journalFilter),
    enabled: Boolean(sellerId),
    placeholderData: (previous) => previous,
  });

  useEffect(() => {
    if (!sellers.some((item) => item.id === sellerId)) setSellerId(sellers[0]?.id ?? "");
  }, [sellerId, sellers]);
  useEffect(() => {
    setActionError("");
    setPaceError("");
    setPaceSaved(null);
    setJournalFilter({ status: "", page: 1 });
  }, [sellerId]);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["support-bot", sellerId] });
    await queryClient.invalidateQueries({ queryKey: ["support-bot-products", sellerId] });
    await queryClient.invalidateQueries({ queryKey: ["support-bot-product-dialogs", sellerId] });
    await queryClient.invalidateQueries({ queryKey: ["support-bot-sends", sellerId] });
  };
  const refreshSellers = async () => {
    await queryClient.invalidateQueries({ queryKey: ["automation-sellers", AUTOMATION_ID] });
    await queryClient.invalidateQueries({ queryKey: ["wb-sellers"] });
  };
  const failed = (fallback: string) => (error: unknown) => setActionError(message(error, fallback));

  const connectMutation = useMutation({
    mutationFn: (payload: { seller_id: string } | SellerInput) => attachSeller(AUTOMATION_ID, payload),
    onSuccess: async (seller) => {
      setConnecting(false);
      setFormError("");
      setSellerId(seller.id);
      await refreshSellers();
    },
    onError: (error) => setFormError(message(error, "Не удалось подключить селлера")),
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
  const modeMutation = useMutation({
    mutationFn: (mode: BotMode) => setMode(sellerId, mode),
    onSuccess: async () => {
      setPendingMode(null);
      setActionError("");
      await refresh();
    },
    onError: failed("Не удалось сменить режим"),
  });
  const paceMutation = useMutation({
    mutationFn: (pace: PaceInput) => savePace(sellerId, pace),
    onSuccess: async (result) => {
      setPaceError("");
      setPaceSaved(result.rescheduled);
      await refresh();
    },
    onError: (error: unknown) => {
      setPaceSaved(null);
      setPaceError(message(error, "Не удалось сохранить интервал"));
    },
  });
  const pauseMutation = useMutation({
    mutationFn: (paused: boolean) => setPaused(paused),
    onSuccess: refresh,
    onError: failed("Не удалось переключить общий выключатель"),
  });
  const addMutation = useMutation({
    mutationFn: (text: string) => addTemplate(sellerId, text),
    onSuccess: async () => {
      setActionError("");
      await refresh();
    },
    onError: failed("Не удалось добавить текст"),
  });
  const saveMutation = useMutation({
    mutationFn: (item: { id: number; text: string }) => updateTemplate(sellerId, item.id, item.text),
    onSuccess: async () => {
      setActionError("");
      await refresh();
    },
    onError: failed("Не удалось сохранить текст"),
  });
  const activeMutation = useMutation({
    mutationFn: (item: { id: number; active: boolean }) => setTemplateActive(sellerId, item.id, item.active),
    onSuccess: refresh,
    onError: failed("Не удалось изменить текст"),
  });
  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteTemplate(sellerId, id),
    onSuccess: refresh,
    onError: failed("Не удалось удалить текст"),
  });
  const selected = sellers.find((seller) => seller.id === sellerId);
  const view = bot.data;
  const hasActiveText = (view?.templates ?? []).some((item) => item.active);

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
                ? `Диалоги ${selected.name}: покупатель ответил, наша сторона ответила, он замолчал — бот напоминает ему в окне 9:00–22:00 по Москве.`
                : "Напоминания покупателям, которые ответили в чате и замолчали."}
            </p>
          </div>
          {view && (
            <div className="review-sync-actions">
              <button
                className="secondary-button"
                disabled={pauseMutation.isPending}
                onClick={() => pauseMutation.mutate(!view.globally_paused)}
                title="Действует на все кабинеты сразу"
              >
                {view.globally_paused ? <Play size={15} /> : <Pause size={15} />}
                {view.globally_paused ? "Снять общую паузу" : "Остановить всех"}
              </button>
            </div>
          )}
        </div>

        {actionError && <div className="inline-error">{actionError}</div>}
        {bot.error && <div className="inline-error">{message(bot.error, "Не удалось загрузить данные")}</div>}
        {view?.mirror_error && (
          <div className="checklist-notice">Лента чатов не читается: {view.mirror_error}</div>
        )}

        {isLoading ? (
          <div className="loading-block">Загружаем данные…</div>
        ) : !selected ? (
          <div className="empty-state">
            <h2>Подключите первый кабинет</h2>
            <p>
              Подключение ничего не отправляет: кабинет стартует с выключенной рассылкой, бот только читает чаты и
              ведёт диалоги. Включить рассылку можно на странице кабинета.
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
        ) : !view ? (
          <div className="loading-block">Загружаем данные…</div>
        ) : (
          <>
            {(view.mode !== "live" || view.globally_paused || view.paused_reason) && (
              <div className="checklist-notice" role="status">
                <strong>{MODE_LABEL[view.mode]}.</strong> {statusLine(view)}{" "}
                {view.mode !== "live" && (
                  <button
                    type="button"
                    className="primary-button"
                    style={{ marginLeft: 8 }}
                    disabled={!hasActiveText}
                    title={!hasActiveText ? "Сначала добавьте активный текст" : undefined}
                    onClick={() => setPendingMode("live")}
                  >
                    Включить рассылку
                  </button>
                )}
              </div>
            )}

            <SupportBotPace
              key={`${view.seller_id}-${view.pace.interval_minutes}-${view.pace.jitter_min_minutes}-${view.pace.jitter_max_minutes}`}
              pace={view.pace}
              defaults={view.pace_default}
              custom={view.pace_custom}
              busy={paceMutation.isPending}
              error={paceError}
              rescheduled={paceSaved}
              onSave={(pace) => paceMutation.mutate(pace)}
            />

            <div className="chats-summary" aria-label="Диалоги по состояниям">
              {STATE_ORDER.map((state) => (
                <article key={state}>
                  <span>{STATE_LABEL[state]}</span>
                  <strong>{view.counts[state] ?? 0}</strong>
                </article>
              ))}
              <article>
                <span>Всего диалогов</span>
                <strong>{totalDialogs(view)}</strong>
              </article>
            </div>

            {(view.monitor_overall === "crit" || view.monitor_overall === "warn") && (
              <div className={`mon-banner mon-banner-${view.monitor_overall}`} role="status">
                <span>
                  {view.monitor_overall === "crit"
                    ? "Мониторинг бота: есть проблема"
                    : "Мониторинг бота: есть замечания"}
                </span>
                {tab !== "monitor" && (
                  <button type="button" className="secondary-button" onClick={() => setTab("monitor")}>
                    Открыть состояние
                  </button>
                )}
              </div>
            )}

            <div className="mode-switch" role="tablist" aria-label="Раздел бота" style={{ marginBottom: 16 }}>
              {(
                [
                  { value: "dialogs", label: "Диалоги и журнал" },
                  { value: "replies", label: "Ответы" },
                  { value: "monitor", label: "Состояние" },
                ] as const
              ).map((option) => (
                <button
                  key={option.value}
                  type="button"
                  role="tab"
                  aria-selected={tab === option.value}
                  className={tab === option.value ? "mode-active" : undefined}
                  onClick={() => setTab(option.value)}
                >
                  {option.label}
                </button>
              ))}
            </div>

            {tab === "monitor" && <SupportBotMonitor sellerId={sellerId} />}
            {tab === "replies" && <SupportBotReplies sellerId={sellerId} gateMode={view.reply_gate_mode} />}
            {tab === "dialogs" && (
              <>
            <h2 className="chats-section-title">Тексты напоминаний</h2>
            <p className="muted chats-legend">
              Бот берёт случайный активный текст и не повторяет один и тот же подряд. До 1 000 символов; в тексте нельзя
              просить удалить или изменить отзыв, обещать деньги за удаление, давать контакты вне WB и угрожать.
            </p>
            <SupportBotTemplates
              templates={view.templates}
              busy={addMutation.isPending || saveMutation.isPending}
              onAdd={(text) => addMutation.mutate(text)}
              onSave={(id, text) => saveMutation.mutate({ id, text })}
              onToggle={(id, active) => activeMutation.mutate({ id, active })}
              onDelete={(id) => deleteMutation.mutate(id)}
            />

            <h2 className="chats-section-title">Диалоги по товарам</h2>
            <SupportBotProducts sellerId={sellerId} onError={setActionError} />

            <h2 className="chats-section-title">Журнал отправок</h2>
            <SupportBotJournal page={sends.data} filter={journalFilter} onFilter={setJournalFilter} />
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
          description={`«${detaching.name}» пропадёт из бота. Журнал и диалоги останутся, отправка по кабинету прекратится.`}
          confirmLabel="Отключить"
          pendingLabel="Отключаем…"
          pending={detachMutation.isPending}
          onClose={() => setDetaching(null)}
          onConfirm={() => detachMutation.mutate(detaching)}
        />
      )}
      {pendingMode && (
        <ConfirmDialog
          title="Включить отправку по всем диалогам?"
          description="Бот начнёт писать покупателям всех диалогов этого кабинета по расписанию: окно 9:00–22:00 по Москве, около 12 сообщений в сутки на диалог. Остановить рассылку можно в любой момент."
          confirmLabel="Включить"
          pendingLabel="Включаем…"
          pending={modeMutation.isPending}
          onClose={() => setPendingMode(null)}
          onConfirm={() => modeMutation.mutate(pendingMode)}
        />
      )}
    </Shell>
  );
}
