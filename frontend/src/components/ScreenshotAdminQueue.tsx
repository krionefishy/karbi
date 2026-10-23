import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "../api/http";
import {
  buildResolve,
  keyAction,
  QUEUE_PERIODS,
  queuePeriodRange,
  selectAfterRemoval,
  stepSelection,
  suspicionBadge,
  toneBadgeClass,
  type QueuePeriod,
  type QueueTab,
} from "../features/screenshotControl/admin";
import { getManagers, getQueue, resolveSubmission } from "../features/screenshotControl/api";
import { formatMoment, periodRange } from "../features/screenshotControl/format";
import { primaryReason, REASON_GROUPS, reasonText } from "../features/screenshotControl/reasons";
import type { AdminSubmission, ReasonCode, ResolveRequest } from "../features/screenshotControl/types";
import { ScreenshotAdminCard, type Panel } from "./ScreenshotAdminCard";

export const QUEUE_KEY = "sca-queue";
const PAGE_SIZE = 50;

const EMPTY: Record<QueueTab, { title: string; text: string }> = {
  review: {
    title: "Очередь пуста",
    text: "Спорные скриншоты появятся здесь, как только конвейер отправит их на проверку.",
  },
  duplicates: {
    title: "Подозрений нет",
    text: "Сюда попадают дубли и совпадения с чужими скриншотами. Пока разбирать нечего.",
  },
  audit: {
    title: "Аудит пуст",
    text: "Случайные принятые скриншоты для перепроверки появятся здесь.",
  },
};

export function ScreenshotAdminQueue({ kind }: { kind: QueueTab }) {
  const queryClient = useQueryClient();
  const [reason, setReason] = useState<ReasonCode | "">("");
  const [userId, setUserId] = useState("");
  const [period, setPeriod] = useState<QueuePeriod>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [resolved, setResolved] = useState<Set<string>>(() => new Set());
  const [panel, setPanel] = useState<Panel>("none");
  const [resolveError, setResolveError] = useState<ApiError | Error | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const range = queuePeriodRange(period);
  const queue = useInfiniteQuery({
    queryKey: [QUEUE_KEY, kind, reason, userId, range.from ?? "", range.to ?? ""],
    queryFn: ({ pageParam }) =>
      getQueue({
        kind,
        reason: reason || undefined,
        user_id: userId || undefined,
        ...range,
        cursor: pageParam || undefined,
        limit: PAGE_SIZE,
      }),
    initialPageParam: "",
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });

  // Список менеджеров для фильтра — из статистики за месяц; удалённых без имени не показываем.
  const month = periodRange("month");
  const managers = useQuery({
    queryKey: ["sca-managers", month.from, month.to],
    queryFn: () => getManagers(month),
    staleTime: 5 * 60_000,
  });

  const items = useMemo(
    () => (queue.data?.pages.flatMap((page) => page.items) ?? []).filter((item) => !resolved.has(item.id)),
    [queue.data, resolved],
  );
  const ids = useMemo(() => items.map((item) => item.id), [items]);
  const selected = items.find((item) => item.id === selectedId) ?? items[0] ?? null;

  const select = (id: string | null) => {
    setSelectedId(id);
    setPanel("none");
    setResolveError(null);
  };

  const resolve = useMutation({
    mutationFn: ({ item, payload }: { item: AdminSubmission; payload: ResolveRequest }) =>
      resolveSubmission(item.id, payload),
    onSuccess: (_result, { item }) => {
      const next = selectAfterRemoval(ids, item.id);
      setResolved((current) => new Set(current).add(item.id));
      select(next);
      void queryClient.invalidateQueries({ queryKey: [QUEUE_KEY, "counts"] });
    },
    onError: (error) => setResolveError(error),
  });

  const submit = (item: AdminSubmission, payload: ResolveRequest) => {
    setResolveError(null);
    resolve.mutate({ item, payload });
  };

  // Клавиши разбора: слушаем окно, но не мешаем вводу в поля (это решает keyAction).
  const handler = useRef<(event: KeyboardEvent) => void>(() => undefined);
  handler.current = (event: KeyboardEvent) => {
    const action = keyAction(event);
    // Пока открыто увеличенное фото или другой диалог, очередь клавишами не листаем.
    if (!action || !selected || resolve.isPending || document.querySelector('[role="dialog"]')) return;
    event.preventDefault();
    if (action === "next" || action === "prev") {
      select(stepSelection(ids, selected.id, action === "next" ? 1 : -1));
      return;
    }
    if (action === "approve") submit(selected, buildResolve(selected, "approve"));
    else setPanel(action === "decline" ? "decline" : "duplicate");
  };
  useEffect(() => {
    const listener = (event: KeyboardEvent) => handler.current(event);
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, []);

  useEffect(() => {
    if (!selected) return;
    listRef.current
      ?.querySelector<HTMLElement>(`[data-id="${selected.id}"]`)
      ?.scrollIntoView?.({ block: "nearest" });
  }, [selected]);

  const managerOptions = (managers.data?.items ?? [])
    .map((stats) => stats.user)
    .filter((user) => user.username)
    .sort((a, b) => a.username.localeCompare(b.username, "ru"));
  const filtered = Boolean(reason || userId || period !== "all");

  return (
    <div className="sca-review">
      <div className="sca-filters">
        <select aria-label="Причина" value={reason} onChange={(event) => setReason(event.target.value as ReasonCode | "")}>
          <option value="">Все причины</option>
          {REASON_GROUPS.map((group) => (
            <optgroup key={group.label} label={group.label}>
              {group.codes.map((code) => (
                <option key={code} value={code}>
                  {reasonText(code)}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <select aria-label="Менеджер" value={userId} onChange={(event) => setUserId(event.target.value)}>
          <option value="">Все менеджеры</option>
          {managerOptions.map((user) => (
            <option key={user.id} value={user.id}>
              {user.username}
            </option>
          ))}
        </select>
        <div className="seg" role="group" aria-label="Период очереди">
          {QUEUE_PERIODS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={period === key ? "seg-active" : undefined}
              aria-pressed={period === key}
              onClick={() => setPeriod(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <span className="sca-hint">
          <kbd>A</kbd> засчитать · <kbd>D</kbd> отклонить · <kbd>N</kbd> дубль · <kbd>←</kbd>
          <kbd>→</kbd> по очереди
        </span>
      </div>

      {queue.isLoading ? (
        <div className="loading-block">Загружаем очередь…</div>
      ) : queue.isError ? (
        <div className="inline-error" role="alert">
          <div>
            <strong>Очередь не загрузилась</strong>
            {queue.error instanceof ApiError ? queue.error.message : "Нет связи с сервером"}
          </div>
          <button type="button" className="secondary-button sca-retry" onClick={() => void queue.refetch()}>
            Повторить
          </button>
        </div>
      ) : items.length === 0 ? (
        <div className="empty-state sca-empty">
          <h2>{filtered ? "Под фильтр ничего не попало" : EMPTY[kind].title}</h2>
          <p>{filtered ? "Снимите часть фильтров, чтобы увидеть остальную очередь." : EMPTY[kind].text}</p>
          {filtered && (
            <button
              type="button"
              className="secondary-button"
              onClick={() => {
                setReason("");
                setUserId("");
                setPeriod("all");
              }}
            >
              Сбросить фильтры
            </button>
          )}
        </div>
      ) : (
        <div className="sca-split">
          <div className="sca-queue" ref={listRef}>
            <div className="sca-queue-head" aria-hidden="true">
              <span>Менеджер · причина</span>
              <span>Время</span>
              <span>Риск</span>
            </div>
            <ul className="sca-queue-list" aria-label="Очередь">
              {items.map((item) => {
                const badge = suspicionBadge(item.suspicion);
                const first = primaryReason(item.reasons);
                const active = item.id === selected?.id;
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      data-id={item.id}
                      className={`sca-queue-row${active ? " sca-queue-row-active" : ""}`}
                      aria-current={active ? "true" : undefined}
                      onClick={() => select(item.id)}
                    >
                      <span className="sca-queue-who">
                        <strong>{item.user.username || "удалённый сотрудник"}</strong>
                        <small>{first ? reasonText(first.code) : "без причины"}</small>
                      </span>
                      <span className="mono sca-queue-time">{formatMoment(item.received_at)}</span>
                      <span>
                        {badge && (
                          <span className={toneBadgeClass(badge.tone)}>
                            <span />
                            {badge.label}
                          </span>
                        )}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
            {queue.hasNextPage && (
              <div className="sca-queue-foot">
                <button
                  type="button"
                  className="secondary-button sca-more"
                  disabled={queue.isFetchingNextPage}
                  onClick={() => void queue.fetchNextPage()}
                >
                  {queue.isFetchingNextPage ? "Загружаем…" : "Показать ещё"}
                </button>
              </div>
            )}
          </div>

          {selected && (
            <ScreenshotAdminCard
              key={selected.id}
              item={selected}
              panel={panel}
              onPanel={(next) => {
                setPanel(next);
                setResolveError(null);
              }}
              onResolve={(payload) => submit(selected, payload)}
              pending={resolve.isPending}
              error={resolveError}
            />
          )}
        </div>
      )}
    </div>
  );
}
