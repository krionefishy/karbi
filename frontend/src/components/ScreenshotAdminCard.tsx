import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, ExternalLink, ImageOff, RotateCcw, Trash2, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { ApiError } from "../api/http";
import {
  actorText,
  agreementBadge,
  approveDecision,
  buildResolve,
  chatTail,
  comparePair,
  confidenceBadge,
  duplicateTargets,
  formatReviewAt,
  noteRequired,
  outcomeText,
  pairTarget,
  sourceUrl,
  transitionText,
  verdictBadge,
  type Action,
  type Badge,
  type DuplicateTarget,
} from "../features/screenshotControl/admin";
import {
  eraseSubmissionFile,
  getAdminImageUrl,
  getAdminSubmission,
  reprocessSubmission,
} from "../features/screenshotControl/api";
import { apiErrorText } from "../features/screenshotControl/errors";
import { formatMoment, plural, toneBadgeClass } from "../features/screenshotControl/format";
import { useStableImage } from "../features/screenshotControl/hooks";
import { forgetImage, originalKey, stableImageUrl, thumbKey } from "../features/screenshotControl/images";
import { reasonsLine } from "../features/screenshotControl/reasons";
import type {
  AdminSubmission,
  AdminSubmissionDetail,
  ImageKind,
  ResolveRequest,
} from "../features/screenshotControl/types";
import { ConfirmDialog } from "./SellerDialog";

export type Panel = "none" | "decline" | "duplicate";

/** Ссылку обновляем за 30 с до истечения; без срока — раз в 4 минуты (TTL на сервере 5 минут). */
const IMAGE_REFRESH_MS = 4 * 60_000;
const IMAGE_MARGIN_MS = 30_000;
/** Сервер ограничивает TTL часом; дальше заглядывать незачем. */
const IMAGE_MAX_REFRESH_MS = 60 * 60_000;
export const IMAGE_KEY = "sca-image";
export const DETAIL_KEY = "sca-detail";

function BadgeView({ badge }: { badge: Badge | null }) {
  if (!badge) return null;
  return (
    <span className={toneBadgeClass(badge.tone)}>
      <span />
      {badge.label}
    </span>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="sca-field">
      <span className="sca-field-label">{label}</span>
      <span className="sca-field-value">{children}</span>
    </div>
  );
}

function refreshIn(expiresAt: string | null | undefined): number {
  const at = expiresAt ? Date.parse(expiresAt) : Number.NaN;
  if (Number.isNaN(at)) return IMAGE_REFRESH_MS;
  return Math.min(IMAGE_MAX_REFRESH_MS, Math.max(IMAGE_MARGIN_MS, at - Date.now() - IMAGE_MARGIN_MS));
}

function useImage(id: string, kind: ImageKind) {
  return useQuery({
    queryKey: [IMAGE_KEY, id, kind],
    queryFn: () => getAdminImageUrl(id, kind),
    staleTime: (query) => refreshIn(query.state.data?.expires_at),
    refetchInterval: (query) => refreshIn(query.state.data?.expires_at),
    retry: false,
  });
}

function Shot({
  src,
  alt,
  onOpen,
  onError,
}: {
  src: string | null;
  alt: string;
  onOpen?: () => void;
  onError?: () => void;
}) {
  if (!src) {
    return (
      <div className="sca-shot sca-shot-empty">
        <ImageOff size={20} aria-hidden="true" />
        <span>Картинки нет: очищена по сроку или ещё не выгружена</span>
      </div>
    );
  }
  const image = <img src={src} alt={alt} onError={onError} />;
  return onOpen ? (
    <button type="button" className="sca-shot" onClick={onOpen} aria-label="Увеличить скриншот">
      {image}
    </button>
  ) : (
    <div className="sca-shot">{image}</div>
  );
}

function errorText(error: ApiError | Error | null): string {
  if (!error) return "";
  if (error instanceof ApiError && error.code === "not_found") return "Скриншот уже разобран или удалён. Обновите очередь.";
  if (error instanceof ApiError) return apiErrorText(error);
  return "Нет связи с сервером. Решение не сохранено.";
}

interface CardProps {
  item: AdminSubmission;
  panel: Panel;
  onPanel: (panel: Panel) => void;
  onResolve: (payload: ResolveRequest) => void;
  pending: boolean;
  error: ApiError | Error | null;
  /** Скриншот ушёл на пересчёт и до нового вердикта покидает очередь. */
  onReprocessed: () => void;
}

type Confirm = "none" | "reprocess" | "erase";

export function ScreenshotAdminCard({ item, panel, onPanel, onResolve, pending, error, onReprocessed }: CardProps) {
  const queryClient = useQueryClient();
  const thumb = useImage(item.id, "thumb");
  const original = useImage(item.id, "original");
  const detail = useQuery({ queryKey: [DETAIL_KEY, item.id], queryFn: () => getAdminSubmission(item.id) });
  const [zoomed, setZoomed] = useState(false);
  const [confirm, setConfirm] = useState<Confirm>("none");
  const [toolNotice, setToolNotice] = useState("");
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState("");
  const targets = duplicateTargets(item);
  const pair = pairTarget(item);
  const [target, setTarget] = useState<string | null>(pair?.id ?? null);
  const [compareOpen, setCompareOpen] = useState(item.verdict === "duplicate" || item.suspicion === "high");

  // Ссылка на картинку держится до истечения: иначе браузер перекачивает её на каждом ответе сервера.
  const shot = useStableImage(thumbKey(item.id), thumb.data ? thumb.data.url : item.thumb_url, {
    expiresAt: thumb.data?.expires_at,
    onExpired: () => void queryClient.invalidateQueries({ queryKey: [IMAGE_KEY, item.id, "thumb"] }),
  });
  const thumbSrc = shot.src;
  const originalUrl = stableImageUrl(originalKey(item.id), original.data?.available ? original.data.url : null, {
    expiresAt: original.data?.expires_at,
  });

  const fields = item.fields;
  const who = item.user.username || "удалённый сотрудник";
  const apiNoteError = error instanceof ApiError && error.code === "note_required";
  const generalError = error && !apiNoteError ? errorText(error) : "";

  const reprocess = useMutation({
    mutationFn: () => reprocessSubmission(item.id),
    onSuccess: () => {
      setConfirm("none");
      onReprocessed();
    },
    onError: (err) => {
      setConfirm("none");
      setToolNotice(`Пересчёт не запущен. ${errorText(err)}`);
    },
  });
  const erase = useMutation({
    mutationFn: () => eraseSubmissionFile(item.id),
    onSuccess: () => {
      setConfirm("none");
      setToolNotice("Файл стирается: оригинал и миниатюра пропадут в течение минуты. Вердикт и поля остались.");
      forgetImage(thumbKey(item.id));
      forgetImage(originalKey(item.id));
      void queryClient.invalidateQueries({ queryKey: [IMAGE_KEY, item.id] });
      void queryClient.invalidateQueries({ queryKey: [DETAIL_KEY, item.id] });
    },
    onError: (err) => {
      setConfirm("none");
      setToolNotice(`Файл не стёрт. ${errorText(err)}`);
    },
  });

  useEffect(() => {
    if (!zoomed) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setZoomed(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [zoomed]);

  const send = (action: Action) => {
    if (noteRequired(action, item) && !note.trim()) {
      setNoteError(action === "decline" ? "Напишите, почему не засчитан" : "Напишите, почему снимаете засчитанный");
      return;
    }
    if (action === "duplicate" && !target) {
      setNoteError("Выберите, чей это дубль");
      return;
    }
    setNoteError("");
    // Комментарий пишут к отклонению и дублю; в «Засчитать» черновик из панели не уходит.
    onResolve(buildResolve(item, action, { note: action === "approve" ? "" : note, target }));
  };

  const markDuplicate = (id: string) => {
    setTarget(id);
    if (noteRequired("duplicate", item) && !note.trim()) {
      onPanel("duplicate");
      return;
    }
    onResolve(buildResolve(item, "duplicate", { note, target: id }));
  };

  const approveLabel = approveDecision(item) === "audit_ok" ? "Подтвердить" : "Засчитать";
  const closePanel = () => {
    onPanel("none");
    setNote("");
    setNoteError("");
  };
  const erased = detail.data ? fileErased(detail.data) : false;

  return (
    <section className="sca-card" aria-label={`Скриншот ${who}`}>
      <Shot
        src={thumbSrc}
        alt={`Скриншот ${who}`}
        onOpen={thumbSrc ? () => setZoomed(true) : undefined}
        onError={shot.onError}
      />
      <div className="sca-shot-actions">
        <span className="muted">
          {formatMoment(item.received_at)} · {item.channel === "extension" ? "расширение" : item.channel === "telegram" ? "Telegram" : "сайт"}
        </span>
        {originalUrl ? (
          <a className="secondary-button sca-small" href={originalUrl} target="_blank" rel="noopener noreferrer">
            <ExternalLink size={14} aria-hidden="true" />
            Открыть оригинал
          </a>
        ) : (
          <span className="sca-muted-small">
            {original.isLoading ? "Готовим ссылку на оригинал…" : "Оригинал недоступен"}
          </span>
        )}
      </div>

      <div className="sca-fields">
        <Field label="Менеджер, подпись">
          {who} · {outcomeText(item.outcome)}
          {item.caption_raw && <span className="sca-muted-small"> «{item.caption_raw}»</span>}
        </Field>
        <Field label="Вердикт">
          <BadgeView badge={verdictBadge(item.verdict)} />
          {item.auto_verdict && item.auto_verdict !== item.verdict && (
            <span className="sca-muted-small">конвейер: {verdictBadge(item.auto_verdict).label.toLowerCase()}</span>
          )}
        </Field>
        <Field label="Чат">
          <span className="mono">{chatTail(fields?.chat_id ?? null)}</span>
          <BadgeView badge={agreementBadge(item.agreement?.chat_id)} />
        </Field>
        <Field label="Время отзыва">
          <span>
            {formatReviewAt(fields?.review_at ?? null, fields?.key_time_precision ?? "minute", fields?.review_tz)}
          </span>
          {fields?.review_tz && fields.review_tz !== "Europe/Moscow" && (
            <span className="sca-muted-small">пояс менеджера: {fields.review_tz}</span>
          )}
          <BadgeView badge={agreementBadge(item.agreement?.time)} />
        </Field>
        <Field label="Текст отзыва">
          <span className="sca-review-text" title={fields?.review_text ?? undefined}>
            {fields?.review_text || "не прочитан"}
          </span>
          <BadgeView badge={confidenceBadge(fields?.confidence.text)} />
        </Field>
        <Field label="Причина">{reasonsLine(item.reasons)}</Field>
        {detail.data && <SourceField detail={detail.data} />}
      </div>

      {panel === "decline" && (
        <div className="sca-panel" onKeyDown={(event) => event.key === "Escape" && closePanel()}>
          <label className="field-label" htmlFor="sca-note">
            Почему не засчитан
          </label>
          <textarea
            id="sca-note"
            rows={3}
            autoFocus
            maxLength={1000}
            value={note}
            aria-invalid={Boolean(noteError || apiNoteError)}
            aria-describedby={noteError || apiNoteError ? "sca-note-error" : undefined}
            onChange={(event) => {
              setNote(event.target.value);
              setNoteError("");
            }}
          />
          {(noteError || apiNoteError) && (
            <p className="form-error" id="sca-note-error" role="alert">
              {noteError || errorText(error)}
            </p>
          )}
          <div className="sca-panel-actions">
            <button type="button" className="danger-button" disabled={pending} onClick={() => send("decline")}>
              Отклонить
            </button>
            <button type="button" className="ghost-button" onClick={closePanel}>
              Отмена
            </button>
          </div>
        </div>
      )}

      {panel === "duplicate" && (
        <div className="sca-panel" onKeyDown={(event) => event.key === "Escape" && closePanel()}>
          <span className="field-label" id={`sca-target-label-${item.id}`}>
            Чей это дубль
          </span>
          {targets.length === 0 ? (
            <p className="sca-muted-small">Совпадений не нашлось: отметить дублем не с чем. Отклоните с комментарием.</p>
          ) : (
            <div className="sca-targets" role="radiogroup" aria-labelledby={`sca-target-label-${item.id}`}>
              {targets.map((candidate, index) => (
                <label key={candidate.id} className="sca-target">
                  <input
                    type="radio"
                    name={`sca-target-${item.id}`}
                    checked={target === candidate.id}
                    autoFocus={index === 0}
                    onChange={() => {
                      setTarget(candidate.id);
                      setNoteError("");
                    }}
                  />
                  <span>
                    {candidate.username}, <span className="mono">{formatMoment(candidate.receivedAt)}</span> ·{" "}
                    {outcomeText(candidate.outcome)}
                  </span>
                  <BadgeView badge={verdictBadge(candidate.verdict)} />
                </label>
              ))}
            </div>
          )}
          <label className="field-label" htmlFor="sca-dup-note">
            {noteRequired("duplicate", item) ? "Комментарий" : "Комментарий, если нужен"}
          </label>
          <textarea
            id="sca-dup-note"
            rows={2}
            maxLength={1000}
            value={note}
            aria-invalid={Boolean(noteError || apiNoteError)}
            aria-describedby={noteError || apiNoteError ? "sca-dup-note-error" : undefined}
            onChange={(event) => {
              setNote(event.target.value);
              setNoteError("");
            }}
          />
          {(noteError || apiNoteError) && (
            <p className="form-error" id="sca-dup-note-error" role="alert">
              {noteError || errorText(error)}
            </p>
          )}
          <div className="sca-panel-actions">
            <button
              type="button"
              className="primary-button"
              disabled={pending || targets.length === 0}
              onClick={() => send("duplicate")}
            >
              Отметить дублем
            </button>
            <button type="button" className="ghost-button" onClick={closePanel}>
              Отмена
            </button>
          </div>
        </div>
      )}

      {generalError && (
        <p className="inline-error sca-error" role="alert">
          {generalError}
        </p>
      )}

      {panel === "none" && (
        <div className="sca-actions">
          <button
            type="button"
            className="primary-button"
            aria-keyshortcuts="A"
            disabled={pending}
            onClick={() => send("approve")}
          >
            {approveLabel} <kbd aria-hidden="true">A</kbd>
          </button>
          <button
            type="button"
            className="secondary-button sca-negative"
            aria-keyshortcuts="D"
            disabled={pending}
            onClick={() => onPanel("decline")}
          >
            Отклонить <kbd aria-hidden="true">D</kbd>
          </button>
          <button
            type="button"
            className="secondary-button"
            aria-keyshortcuts="N"
            disabled={pending}
            onClick={() => onPanel("duplicate")}
          >
            Это дубль… <kbd aria-hidden="true">N</kbd>
          </button>
        </div>
      )}

      {pair && (
        <div className="sca-compare">
          <button
            type="button"
            className="sca-compare-toggle"
            aria-expanded={compareOpen}
            onClick={() => setCompareOpen((open) => !open)}
          >
            {compareOpen ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
            Сравнение с первым скрином
          </button>
          {compareOpen && (
            <Comparison
              item={item}
              pair={pair}
              thumbSrc={thumbSrc}
              pending={pending}
              onDuplicate={markDuplicate}
              onApprove={() => send("approve")}
            />
          )}
        </div>
      )}

      <div className="sca-tools">
        <Journal detail={detail.data} loading={detail.isLoading} failed={detail.isError} />
        <div className="sca-tools-actions">
          <button
            type="button"
            className="ghost-button sca-small"
            disabled={reprocess.isPending}
            onClick={() => {
              setToolNotice("");
              setConfirm("reprocess");
            }}
          >
            <RotateCcw size={14} aria-hidden="true" />
            Пересчитать
          </button>
          <button
            type="button"
            className="ghost-button sca-small sca-danger-text"
            disabled={erase.isPending || !detail.data || erased}
            title={erased ? "Файл уже стёрт" : undefined}
            onClick={() => {
              setToolNotice("");
              setConfirm("erase");
            }}
          >
            <Trash2 size={14} aria-hidden="true" />
            Стереть файл
          </button>
        </div>
      </div>
      {toolNotice && (
        <p className="sca-muted-small sca-tools-notice" role="status">
          {toolNotice}
        </p>
      )}

      {confirm === "reprocess" && (
        <ConfirmDialog
          title="Пересчитать скриншот?"
          description="Скриншот заново пройдёт распознавание и проверку на дубли. Текущий вердикт уйдёт в журнал, а до нового вердикта скриншот пропадёт из очереди."
          confirmLabel="Пересчитать"
          pendingLabel="Запускаем…"
          pending={reprocess.isPending}
          onClose={() => setConfirm("none")}
          onConfirm={() => reprocess.mutate()}
        />
      )}
      {confirm === "erase" && (
        <ConfirmDialog
          danger
          title="Стереть файл скриншота?"
          description={eraseDescription(detail.data)}
          confirmLabel="Стереть"
          pendingLabel="Стираем…"
          pending={erase.isPending}
          onClose={() => setConfirm("none")}
          onConfirm={() => erase.mutate()}
        />
      )}

      {zoomed && (
        <div
          className="modal-overlay sca-zoom"
          role="dialog"
          aria-modal="true"
          aria-label="Скриншот целиком"
          onClick={() => setZoomed(false)}
        >
          <button
            type="button"
            className="dialog-close sca-zoom-close"
            aria-label="Закрыть"
            autoFocus
            onClick={() => setZoomed(false)}
          >
            <X size={18} />
          </button>
          <img src={originalUrl ?? thumbSrc ?? undefined} alt={`Скриншот ${who} целиком`} onClick={(event) => event.stopPropagation()} />
        </div>
      )}
    </section>
  );
}

/** Оригинал и миниатюра уже стёрты или стираются: стирать нечего. */
function fileErased(detail: AdminSubmissionDetail): boolean {
  const objects = detail.file.objects;
  return objects.length > 0 && objects.every((object) => object.state === "deleted" || object.state === "delete_requested");
}

function eraseDescription(detail: AdminSubmissionDetail | undefined): string {
  const base = "Оригинал и миниатюра будут удалены, вердикт останется.";
  const others = (detail?.file.submissions_count ?? 1) - 1;
  return others > 0
    ? `${base} Тот же файл прислан ещё ${others} ${plural(others, ["раз", "раза", "раз"])}: картинка пропадёт и там. Отменить нельзя.`
    : `${base} Отменить нельзя.`;
}

function SourceField({ detail }: { detail: AdminSubmissionDetail }) {
  const source = sourceUrl(detail.client_url);
  if (!source) return null;
  return (
    <Field label="Снято на странице">
      <a className="sca-source mono" href={source.href} target="_blank" rel="noopener noreferrer" title={source.href}>
        {source.text}
      </a>
    </Field>
  );
}

function Journal({
  detail,
  loading,
  failed,
}: {
  detail: AdminSubmissionDetail | undefined;
  loading: boolean;
  failed: boolean;
}) {
  const [open, setOpen] = useState(false);
  const events = detail?.events ?? [];
  const listId = `sca-journal-${detail?.id ?? "pending"}`;
  return (
    <div className="sca-journal">
      <button
        type="button"
        className="sca-compare-toggle"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        disabled={!detail}
        onClick={() => setOpen((value) => !value)}
      >
        {open ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
        Журнал решений
        <span className="sca-muted-small">{loading ? "загружаем…" : failed ? "не загрузился" : events.length}</span>
      </button>
      {open && detail && (
        <ol className="sca-journal-list" id={listId}>
          {events.length === 0 ? (
            <li className="sca-muted-small">Решений ещё не было.</li>
          ) : (
            events.map((event, index) => (
              <li key={`${event.at}-${index}`}>
                <span className="mono sca-journal-time">{formatMoment(event.at)}</span>
                <span className="sca-journal-body">
                  <strong>{transitionText(event.from_verdict, event.to_verdict)}</strong>
                  <span className="sca-muted-small">
                    {actorText(event.actor)}
                    {event.reasons.length > 0 ? ` · ${reasonsLine(event.reasons)}` : ""}
                  </span>
                  {event.note && <span className="sca-journal-note">«{event.note}»</span>}
                </span>
              </li>
            ))
          )}
        </ol>
      )}
    </div>
  );
}

function Comparison({
  item,
  pair,
  thumbSrc,
  pending,
  onDuplicate,
  onApprove,
}: {
  item: AdminSubmission;
  pair: DuplicateTarget;
  thumbSrc: string | null;
  pending: boolean;
  onDuplicate: (id: string) => void;
  onApprove: () => void;
}) {
  const rows = comparePair(item, pair);
  const who = item.user.username || "удалённый сотрудник";
  return (
    <div className="sca-compare-body">
      <div className="sca-pair">
        <figure>
          <figcaption>
            <span>
              Первый: {pair.username}, <span className="mono">{formatMoment(pair.receivedAt)}</span>
            </span>
            <BadgeView badge={verdictBadge(pair.verdict)} />
          </figcaption>
          <Shot src={stableImageUrl(thumbKey(pair.id), pair.thumbUrl)} alt={`Первый скриншот, ${pair.username}`} />
        </figure>
        <figure className="sca-pair-this">
          <figcaption>
            <span>
              Этот: {who}, <span className="mono">{formatMoment(item.received_at)}</span>
            </span>
            <BadgeView badge={verdictBadge(item.verdict)} />
          </figcaption>
          <Shot src={thumbSrc} alt={`Этот скриншот, ${who}`} />
        </figure>
      </div>
      <div className="sca-features">
        {rows.map((row) => (
          <Field key={row.label} label={row.label}>
            {row.badge ? <BadgeView badge={row.badge} /> : row.note}
          </Field>
        ))}
      </div>
      <div className="sca-actions">
        <button type="button" className="secondary-button sca-negative" disabled={pending} onClick={() => onDuplicate(pair.id)}>
          Дубль, не засчитывать
        </button>
        <button type="button" className="primary-button" disabled={pending} onClick={onApprove}>
          Не дубль, засчитать
        </button>
      </div>
    </div>
  );
}
