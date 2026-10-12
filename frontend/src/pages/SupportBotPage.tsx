import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, ExternalLink, Pause, Play } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError } from "../api/http";
import { ConnectSellerDialog } from "../components/ConnectSellerDialog";
import { ConfirmDialog } from "../components/SellerDialog";
import { SellerSwitcher } from "../components/SellerSwitcher";
import { Shell } from "../components/Shell";
import {
  addTemplate,
  deleteTemplate,
  getDialogs,
  getSends,
  getSupportBot,
  PAGE_SIZE,
  setDialogFlags,
  setMode,
  setPaused,
  setTemplateActive,
} from "../features/supportBot/api";
import {
  MODE_LABEL,
  SEND_LABEL,
  STATE_LABEL,
  STATE_ORDER,
  statusLine,
  totalDialogs,
} from "../features/supportBot/format";
import type { BotDialog, DialogFilter, DialogFlag, DialogState } from "../features/supportBot/types";
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

const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });
const stamp = (value: string | null) => (value ? momentFormatter.format(new Date(value)) : "—");

const FLAG_OPTIONS: { value: DialogFlag | ""; label: string }[] = [
  { value: "", label: "Любые флаги" },
  { value: "no_contact", label: "Просили не писать" },
  { value: "attention", label: "Жалоба" },
  { value: "excluded", label: "Исключены вручную" },
];

const TEMPLATE = ["170px", "200px", "150px", "150px", "100px", "minmax(260px, 1fr)"].join(" ");

function message(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

export function SupportBotPage() {
  const queryClient = useQueryClient();
  const [sellerId, setSellerId] = useState("");
  const [filter, setFilter] = useState<DialogFilter>({ state: "", flag: "", page: 1 });
  const [connecting, setConnecting] = useState(false);
  const [detaching, setDetaching] = useState<Seller | null>(null);
  const [liveConfirm, setLiveConfirm] = useState(false);
  const [newText, setNewText] = useState("");
  const [formError, setFormError] = useState("");
  const [actionError, setActionError] = useState("");

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
  const dialogs = useQuery({
    queryKey: ["support-bot-dialogs", sellerId, filter],
    queryFn: () => getDialogs(sellerId, filter),
    enabled: Boolean(sellerId),
    placeholderData: (previous) => previous,
  });
  const sends = useQuery({
    queryKey: ["support-bot-sends", sellerId],
    queryFn: () => getSends(sellerId),
    enabled: Boolean(sellerId),
  });

  useEffect(() => {
    if (!sellers.some((item) => item.id === sellerId)) setSellerId(sellers[0]?.id ?? "");
  }, [sellerId, sellers]);
  useEffect(() => setActionError(""), [sellerId]);

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["support-bot", sellerId] });
    await queryClient.invalidateQueries({ queryKey: ["support-bot-dialogs", sellerId] });
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
    mutationFn: (mode: "dry_run" | "live") => setMode(sellerId, mode),
    onSuccess: async () => {
      setLiveConfirm(false);
      setActionError("");
      await refresh();
    },
    onError: failed("Не удалось сменить режим"),
  });
  const pauseMutation = useMutation({
    mutationFn: (paused: boolean) => setPaused(paused),
    onSuccess: refresh,
    onError: failed("Не удалось переключить общий выключатель"),
  });
  const addMutation = useMutation({
    mutationFn: (text: string) => addTemplate(sellerId, text),
    onSuccess: async () => {
      setNewText("");
      setActionError("");
      await refresh();
    },
    onError: failed("Не удалось добавить текст"),
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
  const flagMutation = useMutation({
    mutationFn: (item: { dialog: BotDialog; flags: { excluded?: boolean; closed?: boolean } }) =>
      setDialogFlags(sellerId, item.dialog, item.flags),
    onSuccess: refresh,
    onError: failed("Не удалось изменить диалог"),
  });

  const selected = sellers.find((seller) => seller.id === sellerId);
  const view = bot.data;
  const page = dialogs.data;
  const changeFilter = (patch: Partial<DialogFilter>) => setFilter({ ...filter, ...patch, page: 1 });
  const live = view?.mode === "live";

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
              Подключение ничего не отправляет: кабинет стартует в режиме «сухой прогон», бот считает, кому и что
              отправил бы, и пишет это в журнал.
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
            <div className="checklist-notice" role="status">
              <strong>{MODE_LABEL[view.mode]}.</strong> {statusLine(view)}
              {view.mirror_read_through && <> · чаты прочитаны {stamp(view.mirror_read_through)}</>}{" "}
              {live ? (
                <button className="secondary-button" onClick={() => modeMutation.mutate("dry_run")}>
                  Вернуть сухой прогон
                </button>
              ) : (
                <button
                  className="primary-button"
                  disabled={view.templates.filter((item) => item.active).length === 0}
                  title={
                    view.templates.some((item) => item.active)
                      ? undefined
                      : "Сначала добавьте хотя бы один активный текст"
                  }
                  onClick={() => setLiveConfirm(true)}
                >
                  Включить отправку
                </button>
              )}
            </div>

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

            <h2 className="chats-section-title">Тексты напоминаний</h2>
            <p className="muted chats-legend">
              Бот берёт случайный активный текст и не повторяет один и тот же подряд. До 1 000 символов; в тексте нельзя
              просить удалить или изменить отзыв, обещать деньги за удаление, давать контакты вне WB и угрожать.
            </p>
            <section className="checklist-scroll stocks-scroll">
              {view.templates.map((item) => (
                <div className="stocks-row" key={item.id} style={{ gridTemplateColumns: "1fr 120px 120px" }}>
                  <span className="chats-reply" style={{ opacity: item.active ? 1 : 0.5 }}>
                    {item.text}
                  </span>
                  <button
                    className="secondary-button"
                    onClick={() => activeMutation.mutate({ id: item.id, active: !item.active })}
                  >
                    {item.active ? "Отключить" : "Включить"}
                  </button>
                  <button className="secondary-button" onClick={() => deleteMutation.mutate(item.id)}>
                    Удалить
                  </button>
                </div>
              ))}
              {view.templates.length === 0 && <span className="fbs-row-empty">Текстов ещё нет.</span>}
            </section>
            <form
              className="checklist-toolbar"
              onSubmit={(event) => {
                event.preventDefault();
                if (newText.trim()) addMutation.mutate(newText);
              }}
            >
              <textarea
                aria-label="Новый текст напоминания"
                rows={3}
                maxLength={1000}
                style={{ width: "100%" }}
                placeholder="Здравствуйте! Подскажите, удалось ли решить вопрос с товаром?"
                value={newText}
                onChange={(event) => setNewText(event.target.value)}
              />
              <button className="primary-button" type="submit" disabled={addMutation.isPending || !newText.trim()}>
                Добавить текст
              </button>
            </form>

            <h2 className="chats-section-title">Диалоги</h2>
            <div className="checklist-toolbar penalties-toolbar">
              <div className="mode-switch penalties-groups" role="tablist" aria-label="Состояния диалогов">
                {([{ value: "", label: "Все" }, ...STATE_ORDER.map((value) => ({ value, label: STATE_LABEL[value] }))] as {
                  value: DialogState | "";
                  label: string;
                }[]).map((option) => (
                  <button
                    key={option.value || "all"}
                    type="button"
                    role="tab"
                    aria-selected={filter.state === option.value}
                    className={filter.state === option.value ? "mode-active" : undefined}
                    onClick={() => changeFilter({ state: option.value })}
                  >
                    {option.label}
                  </button>
                ))}
              </div>
              <label className="penalties-field">
                <select
                  aria-label="Флаги диалога"
                  value={filter.flag}
                  onChange={(event) => changeFilter({ flag: event.target.value as DialogFlag | "" })}
                >
                  {FLAG_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <section
              className="checklist-scroll stocks-scroll"
              style={{ "--stocks-template": TEMPLATE } as React.CSSProperties}
            >
              <div className="stocks-head">
                <span className="checklist-sticky">Диалог</span>
                <span>Состояние</span>
                <span>Покупатель писал</span>
                <span>Мы писали</span>
                <span>Сообщений</span>
                <span>Действия</span>
              </div>
              {page?.dialogs.map((dialog) => (
                <div className="stocks-row" key={`${dialog.chat_id}:${dialog.prompt_at}`}>
                  <span className="checklist-sticky">
                    {dialog.chat_url ? (
                      <a className="wb-article-link" href={dialog.chat_url} target="_blank" rel="noreferrer">
                        {stamp(dialog.prompt_at)} <ExternalLink size={12} />
                      </a>
                    ) : (
                      <span title={dialog.chat_id}>{stamp(dialog.prompt_at)}</span>
                    )}
                    <em>{dialog.nm_id ?? "артикул не передан"}</em>
                  </span>
                  <span className="penalty-kind">
                    <strong>{STATE_LABEL[dialog.state]}</strong>
                    {dialog.no_contact && <em className="stocks-unknown">просил не писать</em>}
                    {dialog.attention && <em className="stocks-unknown">жалоба</em>}
                    {dialog.excluded && <em className="stocks-unknown">исключён</em>}
                    {dialog.next_send_at && dialog.state === "in_campaign" && (
                      <em>следующее: {stamp(dialog.next_send_at)}</em>
                    )}
                  </span>
                  <span>{stamp(dialog.buyer_last_at)}</span>
                  <span>{stamp(dialog.last_sent_at ?? dialog.our_last_at)}</span>
                  <span>{dialog.sent_count}</span>
                  <span>
                    <button
                      className="secondary-button"
                      onClick={() => flagMutation.mutate({ dialog, flags: { excluded: !dialog.excluded } })}
                    >
                      {dialog.excluded ? "Вернуть в рассылку" : "Исключить"}
                    </button>{" "}
                    <button
                      className="secondary-button"
                      onClick={() => flagMutation.mutate({ dialog, flags: { closed: !dialog.closed } })}
                    >
                      {dialog.closed ? "Открыть" : "Закрыть"}
                    </button>
                  </span>
                </div>
              ))}
              {page && page.dialogs.length === 0 && <span className="fbs-row-empty">Под этот фильтр диалогов нет.</span>}
            </section>
            {page && page.total > PAGE_SIZE && (
              <nav className="penalties-pager" aria-label="Страницы">
                <button
                  type="button"
                  className="secondary-button"
                  disabled={filter.page <= 1}
                  onClick={() => setFilter({ ...filter, page: filter.page - 1 })}
                >
                  <ChevronLeft size={15} /> Назад
                </button>
                <span>
                  {(page.page - 1) * page.page_size + 1}–{Math.min(page.page * page.page_size, page.total)} из{" "}
                  {page.total}
                </span>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={page.page * page.page_size >= page.total}
                  onClick={() => setFilter({ ...filter, page: filter.page + 1 })}
                >
                  Вперёд <ChevronRight size={15} />
                </button>
              </nav>
            )}

            <h2 className="chats-section-title">Журнал отправок</h2>
            <section className="checklist-scroll stocks-scroll">
              {(sends.data ?? []).map((item) => (
                <div className="stocks-row" key={item.id} style={{ gridTemplateColumns: "150px 150px 1fr" }}>
                  <span>{stamp(item.sent_at ?? item.planned_at)}</span>
                  <span className="penalty-kind">
                    <strong>{SEND_LABEL[item.status]}</strong>
                    {item.error && <em className="stocks-unknown">{item.error}</em>}
                  </span>
                  <span className="chats-reply">{item.text}</span>
                </div>
              ))}
              {(sends.data ?? []).length === 0 && <span className="fbs-row-empty">Отправок ещё не было.</span>}
            </section>
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
      {liveConfirm && (
        <ConfirmDialog
          title="Включить отправку?"
          description="Бот начнёт писать покупателям этого кабинета по расписанию: окно 9:00–22:00 по Москве, около 12 сообщений в сутки на диалог. Вернуть сухой прогон можно в любой момент."
          confirmLabel="Включить"
          pendingLabel="Включаем…"
          pending={modeMutation.isPending}
          onClose={() => setLiveConfirm(false)}
          onConfirm={() => modeMutation.mutate("live")}
        />
      )}
    </Shell>
  );
}
