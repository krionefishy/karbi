import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ImageOff } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api/http";
import { getSubmissions, setOutcome } from "../features/screenshotControl/api";
import {
  formatMoment,
  hasPending,
  OUTCOMES,
  outcomeLabel,
  STATUS_FILTERS,
  statusBadgeClass,
  statusLabel,
  submissionNote,
} from "../features/screenshotControl/format";
import type { Outcome, Status, Submission } from "../features/screenshotControl/types";

export const SUBMISSIONS_KEY = "sc-submissions";
export const POLL_MS = 5000;
const PAGE_SIZE = 20;

export function ScreenshotList() {
  const [status, setStatus] = useState<Status | "">("");
  const [outcome, setOutcomeFilter] = useState<Outcome | "">("");

  const query = useInfiniteQuery({
    queryKey: [SUBMISSIONS_KEY, status, outcome],
    queryFn: ({ pageParam }) =>
      getSubmissions({
        status: status || undefined,
        outcome: outcome || undefined,
        cursor: pageParam,
        limit: PAGE_SIZE,
      }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: (current) =>
      hasPending(current.state.data?.pages.flatMap((page) => page.items) ?? []) ? POLL_MS : false,
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  const filtered = Boolean(status || outcome);

  return (
    <section className="card sc-list" aria-label="Последние отправки">
      <div className="card-head">
        <div className="card-head-title">
          <h2>Последние отправки</h2>
          {hasPending(items) && <span className="muted">обновляется</span>}
        </div>
      </div>

      {query.isLoading ? (
        <div className="sc-list-message muted">Загружаем отправки…</div>
      ) : query.isError ? (
        <div className="sc-list-message">
          <div className="inline-error" role="alert">
            <div>
              <strong>Не удалось загрузить отправки</strong>
              {query.error instanceof ApiError ? query.error.message : "Сервер не ответил."}{" "}
              <button className="secondary-button docs-retry" onClick={() => void query.refetch()}>
                Повторить
              </button>
            </div>
          </div>
        </div>
      ) : items.length === 0 ? (
        <div className="card-empty">
          <strong>{filtered ? "Под фильтр ничего не подходит" : "Отправок пока нет"}</strong>
          <p>
            {filtered
              ? "Сбросьте фильтры, чтобы увидеть все отправки."
              : "Перетащите скриншот в зону слева или вставьте его из буфера."}
          </p>
        </div>
      ) : (
        <ul className="sc-rows">
          {items.map((item) => (
            <SubmissionRow key={item.id} item={item} />
          ))}
        </ul>
      )}

      <div className="sc-list-foot">
        <select
          aria-label="Фильтр по статусу"
          value={status}
          onChange={(event) => setStatus(event.target.value as Status | "")}
        >
          <option value="">Все статусы</option>
          {STATUS_FILTERS.map((value) => (
            <option key={value} value={value}>
              {statusLabel(value)}
            </option>
          ))}
        </select>
        <select
          aria-label="Фильтр по исходу"
          value={outcome}
          onChange={(event) => setOutcomeFilter(event.target.value as Outcome | "")}
        >
          <option value="">Все исходы</option>
          {OUTCOMES.map((value) => (
            <option key={value} value={value}>
              {outcomeLabel(value)}
            </option>
          ))}
        </select>
        {query.hasNextPage && (
          <button
            type="button"
            className="secondary-button sc-more"
            disabled={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            {query.isFetchingNextPage ? "Загружаем…" : "Показать ещё"}
          </button>
        )}
      </div>
    </section>
  );
}

function SubmissionRow({ item }: { item: Submission }) {
  const queryClient = useQueryClient();
  const [broken, setBroken] = useState(false);
  const note = submissionNote(item);
  const mutation = useMutation({
    mutationFn: (outcome: Outcome) => setOutcome(item.id, outcome),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [SUBMISSIONS_KEY] }),
  });

  return (
    <li className="sc-row">
      {item.thumb_url && !broken ? (
        <img
          className="sc-thumb"
          src={item.thumb_url}
          alt=""
          width={44}
          height={32}
          loading="lazy"
          onError={() => setBroken(true)}
        />
      ) : (
        <span className="sc-thumb sc-thumb-empty" aria-hidden="true">
          <ImageOff size={14} />
        </span>
      )}
      <div className="sc-row-text">
        <span className="sc-row-title">
          <span className="mono">{formatMoment(item.received_at)}</span> · {outcomeLabel(item.outcome)}
        </span>
        {note && <small className={note.reason ? "sc-row-reason" : "muted mono"}>{note.text}</small>}
        {mutation.isError && (
          <small className="sc-queue-error">
            {mutation.error instanceof ApiError ? mutation.error.message : "Не удалось сохранить исход"}
          </small>
        )}
      </div>
      {item.status === "awaiting_outcome" && (
        <div className="seg sc-outcome-seg" role="group" aria-label="Выбрать исход">
          {OUTCOMES.map((value) => (
            <button key={value} type="button" disabled={mutation.isPending} onClick={() => mutation.mutate(value)}>
              {outcomeLabel(value)}
            </button>
          ))}
        </div>
      )}
      <span className={statusBadgeClass(item.status)}>
        <span />
        {statusLabel(item.status)}
      </span>
    </li>
  );
}
