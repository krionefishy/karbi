import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download } from "lucide-react";
import { useEffect, useState } from "react";

import { ApiError } from "../api/http";
import { ConnectSellerDialog } from "../components/ConnectSellerDialog";
import { ReviewChatsDays } from "../components/ReviewChatsDays";
import { ReviewChatsDialogs } from "../components/ReviewChatsDialogs";
import { ConfirmDialog } from "../components/SellerDialog";
import { SellerSwitcher } from "../components/SellerSwitcher";
import { Shell } from "../components/Shell";
import { downloadReviewChats, getReviewChats } from "../features/reviewChats/api";
import { defaultPeriod, percent, rateShift, share } from "../features/reviewChats/format";
import type {
  DialogGroup,
  DialogOutcome,
  GroupSummary,
  ReviewChats,
  ReviewChatsFilter,
} from "../features/reviewChats/types";
import {
  attachSeller,
  detachSeller,
  getAutomationSellers,
  getSellers,
  retrySellerSync,
} from "../features/sellers/api";
import type { Seller, SellerInput } from "../features/sellers/types";

const AUTOMATION_ID = "wb-review-chats";
const AUTOMATION_TITLE = "Чаты после отзыва";

const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" });

const GROUP_OPTIONS: { value: DialogGroup | ""; label: string }[] = [
  { value: "", label: "Все диалоги" },
  { value: "followed", label: "С нашим сообщением" },
  { value: "bare", label: "Без нашего сообщения" },
];

const OUTCOME_OPTIONS: { value: DialogOutcome | ""; label: string }[] = [
  { value: "", label: "Любой исход" },
  { value: "replied", label: "Ответили" },
  { value: "silent", label: "Не ответили" },
  { value: "pending", label: "Ждём ответа" },
];

function SummaryCard({ title, summary, hint }: { title: string; summary: GroupSummary; hint: string }) {
  return (
    <article>
      <span>{title}</span>
      <strong>
        {percent(summary.reply_rate)}
        <small>ответили · {share(summary.replied, summary)}</small>
      </strong>
      <dl>
        <dt>Диалогов</dt>
        <dd>{summary.total}</dd>
        <dt>Ответили</dt>
        <dd>{summary.replied}</dd>
        <dt>Не ответили</dt>
        <dd>
          {summary.silent} · {percent(summary.silent_rate)}
        </dd>
        <dt>Ждём ответа</dt>
        <dd>{summary.pending}</dd>
      </dl>
      <p>{hint}</p>
    </article>
  );
}

/** Почему цифрам пока нельзя верить: лента не читалась, история догоняется или сбор падает. */
function collectionNotice(chats: ReviewChats): string | null {
  if (chats.collection_error) return `Лента чатов не читается: ${chats.collection_error}`;
  if (chats.synced_through) return null;
  if (chats.history_from) {
    const from = momentFormatter.format(new Date(chats.history_from));
    return `История чатов ещё загружается порциями — сейчас есть события с ${from}. Цифры за последние дни неполные.`;
  }
  return "Лента чатов этого кабинета ещё не читалась: первые данные появятся в течение получаса.";
}

export function ReviewChatsPage() {
  const queryClient = useQueryClient();
  const [sellerId, setSellerId] = useState("");
  const [filter, setFilter] = useState<ReviewChatsFilter>({ ...defaultPeriod(), group: "", outcome: "", page: 1 });
  /** Любая смена фильтра возвращает на первую страницу: нумерация старого списка к новому не относится. */
  const changeFilter = (patch: Partial<ReviewChatsFilter>) => setFilter({ ...filter, ...patch, page: 1 });
  const [connecting, setConnecting] = useState(false);
  const [detaching, setDetaching] = useState<Seller | null>(null);
  const [formError, setFormError] = useState("");
  const [actionError, setActionError] = useState("");
  const invalidRange = filter.dateFrom > filter.dateTo;

  const { data: sellers = [], isLoading } = useQuery({
    queryKey: ["automation-sellers", AUTOMATION_ID],
    queryFn: () => getAutomationSellers(AUTOMATION_ID),
  });
  const { data: registry = [] } = useQuery({
    queryKey: ["wb-sellers", false],
    queryFn: () => getSellers(),
    enabled: connecting,
  });
  const {
    data: chats,
    isLoading: chatsLoading,
    isPlaceholderData: chatsStale,
    error: chatsError,
  } = useQuery({
    queryKey: ["review-chats", sellerId, filter],
    queryFn: () => getReviewChats(sellerId, filter),
    enabled: Boolean(sellerId) && !invalidRange,
    // Смена страницы и фильтра списка не должна схлопывать сводку над ним.
    placeholderData: (previous) => (previous?.seller_id === sellerId ? previous : undefined),
  });

  useEffect(() => {
    if (!sellers.some((item) => item.id === sellerId)) {
      setSellerId(sellers[0]?.id ?? "");
    }
  }, [sellerId, sellers]);
  useEffect(() => setActionError(""), [sellerId]);

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
  const exportMutation = useMutation({
    mutationFn: () => downloadReviewChats(sellerId, filter),
    onSuccess: ({ blob, filename }) => {
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename ?? "review_chats.xlsx";
      link.click();
      URL.revokeObjectURL(url);
      setActionError("");
    },
    onError: (error) => setActionError(error instanceof ApiError ? error.message : "Не удалось выгрузить отчёт"),
  });

  const selected = sellers.find((seller) => seller.id === sellerId);
  const notice = chats ? collectionNotice(chats) : null;
  const shift = chats ? rateShift(chats.followed, chats.bare) : null;

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
                  ? `Диалоги, которые WB открыл с покупателями ${selected.name} после отзыва с низкой оценкой: кто ответил на наше сообщение следом, а кто промолчал.`
                  : "Кто из покупателей отвечает на наше сообщение после автосообщения WB об отзыве."}
              </p>
            </div>
            {selected && (
              <div className="review-sync-actions">
                <div className="last-sync">
                  <span className="eyebrow">Чаты прочитаны</span>
                  <strong>{chats?.synced_through ? momentFormatter.format(new Date(chats.synced_through)) : "—"}</strong>
                </div>
                <button
                  className="primary-button"
                  disabled={exportMutation.isPending || invalidRange}
                  onClick={() => exportMutation.mutate()}
                  title="Сводка по дням и все диалоги за выбранный период"
                >
                  <Download size={15} />
                  {exportMutation.isPending ? "Собираем…" : "Выгрузить в Excel"}
                </button>
              </div>
            )}
          </div>

          {actionError && <div className="inline-error">{actionError}</div>}
          {chatsError && (
            <div className="inline-error">
              {chatsError instanceof ApiError ? chatsError.message : "Не удалось загрузить отчёт"}
            </div>
          )}
          {notice && <div className="checklist-notice">{notice}</div>}

          {isLoading ? (
            <div className="loading-block">Загружаем данные…</div>
          ) : !selected ? (
            <div className="empty-state">
              <h2>Подключите первый кабинет</h2>
              <p>
                Возьмите селлера из реестра. Лента чатов читается для всех кабинетов с доступом к «Чату с
                покупателями», поэтому отчёт покажет и историю до подключения.
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
              <div className="checklist-toolbar penalties-toolbar">
                <label className="penalties-field">
                  <span className="field-label">С</span>
                  <input
                    type="date"
                    value={filter.dateFrom}
                    onChange={(event) => changeFilter({ dateFrom: event.target.value })}
                  />
                </label>
                <label className="penalties-field">
                  <span className="field-label">По</span>
                  <input
                    type="date"
                    value={filter.dateTo}
                    onChange={(event) => changeFilter({ dateTo: event.target.value })}
                  />
                </label>
              </div>
              {invalidRange ? (
                <p className="form-error" role="alert">
                  Начало периода позже его конца
                </p>
              ) : chatsLoading || !chats ? (
                !chatsError && <div className="loading-block">Загружаем данные…</div>
              ) : (
                // Пока грузится новый период, прежние цифры остаются на экране, но приглушёнными.
                <div className={chatsStale ? "chats-stale" : undefined} aria-busy={chatsStale}>
                  <div className="chats-summary" aria-label="Сводка за период">
                    <SummaryCard
                      title="С нашим сообщением"
                      summary={chats.followed}
                      hint={
                        chats.first_follow_up_at
                          ? `Первое наше сообщение в периоде — ${momentFormatter.format(new Date(chats.first_follow_up_at))}.`
                          : "В этом периоде наше сообщение не уходило ни в один диалог."
                      }
                    />
                    <SummaryCard
                      title="Без нашего сообщения"
                      summary={chats.bare}
                      hint={
                        chats.follow_up_late
                          ? `Сюда же попали ${chats.follow_up_late} диал., где покупатель ответил раньше, чем ушло наше сообщение.`
                          : "Диалоги до запуска рассылки и те, куда наше сообщение не ушло."
                      }
                    />
                    <article>
                      <span>Разница</span>
                      <strong>
                        {shift === null ? "—" : `${shift > 0 ? "+" : ""}${shift.toLocaleString("ru-RU")} п.п.`}
                      </strong>
                      <p>
                        Насколько доля ответивших с нашим сообщением отличается от доли без него. Ответ
                        засчитывается в течение {chats.reply_window_hours} ч; диалоги, где окно ещё не истекло, в
                        проценты не входят.
                      </p>
                    </article>
                  </div>

                  <h2 className="chats-section-title">По дням</h2>
                  <ReviewChatsDays days={chats.days} />

                  <h2 className="chats-section-title">Диалоги</h2>
                  <div className="checklist-toolbar penalties-toolbar">
                    <div className="mode-switch penalties-groups" role="tablist" aria-label="Группы диалогов">
                      {GROUP_OPTIONS.map((option) => (
                        <button
                          key={option.value}
                          type="button"
                          role="tab"
                          aria-selected={filter.group === option.value}
                          className={filter.group === option.value ? "mode-active" : undefined}
                          onClick={() => changeFilter({ group: option.value })}
                        >
                          {option.label}
                        </button>
                      ))}
                    </div>
                    <label className="penalties-field">
                      <select
                        aria-label="Исход диалога"
                        value={filter.outcome}
                        onChange={(event) => changeFilter({ outcome: event.target.value as DialogOutcome | "" })}
                      >
                        {OUTCOME_OPTIONS.map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <ReviewChatsDialogs dialogs={chats.dialogs} empty="Под этот фильтр диалогов нет." />
                  {chats.total_dialogs > chats.page_size && (
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
                        {(chats.page - 1) * chats.page_size + 1}–
                        {Math.min(chats.page * chats.page_size, chats.total_dialogs)} из {chats.total_dialogs}
                      </span>
                      <button
                        type="button"
                        className="secondary-button"
                        disabled={chats.page * chats.page_size >= chats.total_dialogs}
                        onClick={() => setFilter({ ...filter, page: filter.page + 1 })}
                      >
                        Вперёд <ChevronRight size={15} />
                      </button>
                    </nav>
                  )}
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
          description={`«${detaching.name}» пропадёт из отчёта. Лента чатов продолжит читаться, и после повторного подключения история будет на месте.`}
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
