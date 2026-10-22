import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, ExternalLink, ImageOff, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { ApiError } from "../api/http";
import {
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
  toneBadgeClass,
  verdictBadge,
  type Action,
  type Badge,
  type DuplicateTarget,
} from "../features/screenshotControl/admin";
import { getAdminImageUrl } from "../features/screenshotControl/api";
import { formatMoment } from "../features/screenshotControl/format";
import { reasonsLine } from "../features/screenshotControl/reasons";
import type { AdminSubmission, ResolveRequest } from "../features/screenshotControl/types";

export type Panel = "none" | "decline" | "duplicate";

/** Presigned-ссылка живёт 5 минут; берём свежую чуть раньше. */
const IMAGE_STALE_MS = 4 * 60_000;

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

function useImage(id: string, kind: "thumb" | "original") {
  return useQuery({
    queryKey: ["sca-image", id, kind],
    queryFn: () => getAdminImageUrl(id, kind),
    staleTime: IMAGE_STALE_MS,
    refetchInterval: IMAGE_STALE_MS,
    retry: false,
  });
}

function Shot({ src, alt, onOpen }: { src: string | null; alt: string; onOpen?: () => void }) {
  if (!src) {
    return (
      <div className="sca-shot sca-shot-empty">
        <ImageOff size={20} />
        <span>Картинки нет: очищена по сроку или ещё не выгружена</span>
      </div>
    );
  }
  const image = <img src={src} alt={alt} />;
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
  if (error instanceof ApiError) {
    if (error.code === "note_required") return "Без комментария это решение не принять: напишите причину.";
    if (error.code === "exact_key_conflict")
      return "Такой кейс уже засчитан у другого скриншота. Засчитать второй раз нельзя, можно отметить дублем.";
    if (error.code === "duplicate_target_invalid") return "Этот скриншот не подходит как оригинал для дубля.";
    if (error.code === "not_found") return "Скриншот уже разобран или удалён. Обновите очередь.";
    return error.message;
  }
  return "Нет связи с сервером. Решение не сохранено.";
}

interface CardProps {
  item: AdminSubmission;
  panel: Panel;
  onPanel: (panel: Panel) => void;
  onResolve: (payload: ResolveRequest) => void;
  pending: boolean;
  error: ApiError | Error | null;
}

export function ScreenshotAdminCard({ item, panel, onPanel, onResolve, pending, error }: CardProps) {
  const thumb = useImage(item.id, "thumb");
  const original = useImage(item.id, "original");
  const [zoomed, setZoomed] = useState(false);
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState("");
  const targets = duplicateTargets(item);
  const pair = pairTarget(item);
  const [target, setTarget] = useState<string | null>(pair?.id ?? null);
  const [compareOpen, setCompareOpen] = useState(item.verdict === "duplicate" || item.suspicion === "high");

  const thumbSrc = thumb.data?.url ?? item.thumb_url;
  const originalUrl = original.data?.available ? original.data.url : null;
  const fields = item.fields;
  const who = item.user.username || "удалённый сотрудник";
  const apiNoteError = error instanceof ApiError && error.code === "note_required";
  const generalError = error && !apiNoteError ? errorText(error) : "";

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
    onResolve(buildResolve(item, action, { note, target }));
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
    setNoteError("");
  };

  return (
    <section className="sca-card" aria-label={`Скриншот ${who}`}>
      <Shot src={thumbSrc} alt={`Скриншот ${who}`} onOpen={thumbSrc ? () => setZoomed(true) : undefined} />
      <div className="sca-shot-actions">
        <span className="muted">
          {formatMoment(item.received_at)} · {item.channel === "extension" ? "расширение" : item.channel === "telegram" ? "Telegram" : "сайт"}
        </span>
        {originalUrl ? (
          <a className="secondary-button sca-small" href={originalUrl} target="_blank" rel="noopener noreferrer">
            <ExternalLink size={14} />
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
          <span>{formatReviewAt(fields?.review_at ?? null, fields?.key_time_precision ?? "minute")}</span>
          <BadgeView badge={agreementBadge(item.agreement?.time)} />
        </Field>
        <Field label="Текст отзыва">
          <span className="sca-review-text" title={fields?.review_text ?? undefined}>
            {fields?.review_text || "не прочитан"}
          </span>
          <BadgeView badge={confidenceBadge(fields?.confidence.text)} />
        </Field>
        <Field label="Причина">{reasonsLine(item.reasons)}</Field>
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
            onChange={(event) => {
              setNote(event.target.value);
              setNoteError("");
            }}
          />
          {(noteError || apiNoteError) && (
            <p className="form-error" role="alert">
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
          <span className="field-label">Чей это дубль</span>
          {targets.length === 0 ? (
            <p className="sca-muted-small">Совпадений не нашлось: отметить дублем не с чем. Отклоните с комментарием.</p>
          ) : (
            <div className="sca-targets" role="radiogroup" aria-label="Оригинал">
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
            onChange={(event) => {
              setNote(event.target.value);
              setNoteError("");
            }}
          />
          {(noteError || apiNoteError) && (
            <p className="form-error" role="alert">
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
          <button type="button" className="primary-button" disabled={pending} onClick={() => send("approve")}>
            {approveLabel} <kbd>A</kbd>
          </button>
          <button type="button" className="secondary-button sca-negative" disabled={pending} onClick={() => onPanel("decline")}>
            Отклонить <kbd>D</kbd>
          </button>
          <button type="button" className="secondary-button" disabled={pending} onClick={() => onPanel("duplicate")}>
            Это дубль… <kbd>N</kbd>
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
            {compareOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
            Сравнение с первым скрином
          </button>
          {compareOpen && <Comparison item={item} pair={pair} thumbSrc={thumbSrc} pending={pending} onDuplicate={markDuplicate} onApprove={() => send("approve")} />}
        </div>
      )}

      {zoomed && (
        <div className="modal-overlay sca-zoom" role="dialog" aria-label="Скриншот целиком" onClick={() => setZoomed(false)}>
          <button type="button" className="dialog-close sca-zoom-close" aria-label="Закрыть" onClick={() => setZoomed(false)}>
            <X size={18} />
          </button>
          <img src={originalUrl ?? thumbSrc ?? undefined} alt={`Скриншот ${who} целиком`} onClick={(event) => event.stopPropagation()} />
        </div>
      )}
    </section>
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
          <Shot src={pair.thumbUrl} alt={`Первый скриншот, ${pair.username}`} />
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
