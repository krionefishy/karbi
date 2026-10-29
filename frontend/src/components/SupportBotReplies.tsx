import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api/http";
import {
  getReplyDialog,
  getReplyEscalations,
  getReplySummary,
  setDialogFlags,
} from "../features/supportBot/api";
import {
  GATE_NOTE,
  armRates,
  formatMinutes,
  percent,
  replyLabel,
  replyTone,
  wilsonText,
} from "../features/supportBot/format";
import type {
  ArmStats,
  EscalationItem,
  ReplyBucket,
  ReplyCategoryCount,
  ReplyGateMode,
  ReplySummary,
} from "../features/supportBot/types";
import {
  ACTION_COLOR,
  CategoryDonut,
  DailyBars,
  Donut,
  RateLines,
} from "./SupportBotRepliesCharts";

const PERIODS = [
  { days: 7, label: "7 дней" },
  { days: 14, label: "14 дней" },
  { days: 30, label: "30 дней" },
  { days: 365, label: "С начала" },
];

const momentFormatter = new Intl.DateTimeFormat("ru-RU", {
  dateStyle: "short",
  timeStyle: "short",
});
const stamp = (value: string | null) =>
  value ? momentFormatter.format(new Date(value)) : "—";
const dayFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium" });

const ROLE_LABEL = {
  buyer: "Покупатель",
  seller: "Продавец",
  bot: "Бот",
  wb: "WB",
} as const;
const ACTION_LABEL = {
  continue: "продолжаем",
  stop: "останавливаем",
  wait: "ждём",
} as const;
const STATUS_LABEL = {
  pending: "ждёт оценки",
  done: "оценён",
  failed: "ошибка оценки",
} as const;

type Source = "dialogs" | "slices";
type Span = "all" | "before" | "after";

function message(error: unknown, fallback: string) {
  return error instanceof ApiError ? error.message : fallback;
}

function Chip({ bucket }: { bucket: ReplyBucket }) {
  return (
    <span className={`rp-chip rp-chip-${replyTone(bucket)}`}>
      {replyLabel(bucket)}
    </span>
  );
}

function Switch<T extends string | number>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="mode-switch" role="tablist" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="tab"
          aria-selected={value === option.value}
          className={value === option.value ? "mode-active" : undefined}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function ArmColumn({ title, arm }: { title: string; arm: ArmStats }) {
  const rates = armRates(arm);
  const row = (
    name: string,
    replied: number,
    eligible: number,
    rate: number | null,
  ) => (
    <div className="rp-arm-row">
      <span>{name}</span>
      <strong>{rate === null ? "—" : percent(replied, eligible)}</strong>
      <small>
        {replied}/{eligible}
        {eligible > 0 && <> · 95%: {wilsonText(replied, eligible)}</>}
      </small>
    </div>
  );
  return (
    <article>
      <h4>{title}</h4>
      <span className="muted">Диалогов: {arm.dialogs}</span>
      {row("Ответили за 48 ч", arm.replied48, arm.eligible48, rates.rate48)}
      {row("Ответили за 24 ч", arm.replied24, arm.eligible24, rates.rate24)}
    </article>
  );
}

function Tiles({ summary }: { summary: ReplySummary }) {
  const { late_sends: late, continues, health } = summary;
  const lateAlert = late.count > 0;
  const contShare =
    continues.dialogs > 0 ? continues.escalated_after / continues.dialogs : 0;
  const contWarn = continues.max_per_dialog >= 5 || contShare >= 0.1;
  return (
    <div className="chats-summary rp-tiles" aria-label="Контрольные показатели">
      <article>
        <span>Эскалации без ответа менеджера</span>
        <strong>{summary.escalations_open}</strong>
      </article>
      <article className={lateAlert ? "rp-alert" : undefined}>
        <span>Отправки после ответа</span>
        <strong>{late.count}</strong>
        <p>
          из {late.total_sends} отправок · задержка: медиана{" "}
          {formatMinutes(late.median_lag_minutes)}, максимум{" "}
          {formatMinutes(late.max_lag_minutes)}
        </p>
        {lateAlert && (
          <p className="rp-flag">Бот писал после ответа покупателя</p>
        )}
      </article>
      <article className={contWarn ? "rp-warn" : undefined}>
        <span>Продолжения</span>
        <strong>{continues.dialogs}</strong>
        <dl>
          <dt>Эскалировали после</dt>
          <dd>{continues.escalated_after}</dd>
          <dt>Решились после</dt>
          <dd>{continues.resolved_after}</dd>
          <dt>Максимум на диалог</dt>
          <dd>{continues.max_per_dialog}</dd>
        </dl>
        {contWarn && (
          <p className="rp-flag">
            Продолжений слишком много, проверьте классификатор
          </p>
        )}
      </article>
      <article className={health.stuck > 0 ? "rp-alert" : undefined}>
        <span>Здоровье классификатора</span>
        <strong>{health.total}</strong>
        <dl>
          <dt>Ждут оценки</dt>
          <dd>{health.pending}</dd>
          <dt>Зависли</dt>
          <dd>{health.stuck}</dd>
          <dt>Ошибки</dt>
          <dd>{health.failed}</dd>
          <dt>Не понятно</dt>
          <dd>{health.unclear}</dd>
          <dt>Медиана решения</dt>
          <dd>{formatMinutes(health.median_decision_minutes)}</dd>
          <dt>Токены вход / выход</dt>
          <dd>
            {health.tokens_in} / {health.tokens_out}
          </dd>
          <dt>Вызовов</dt>
          <dd>{health.calls}</dd>
        </dl>
        {health.stuck > 0 && <p className="rp-flag">Есть зависшие оценки</p>}
      </article>
    </div>
  );
}

function Meaning({ summary }: { summary: ReplySummary }) {
  const [source, setSource] = useState<Source>("dialogs");
  const [span, setSpan] = useState<Span>("all");
  const hasLive = summary.live_since !== null;
  const effectiveSpan = hasLive ? span : "all";
  let items: ReplyCategoryCount[];
  if (effectiveSpan === "before") items = summary.categories_before;
  else if (effectiveSpan === "after") items = summary.categories_after;
  else
    items =
      source === "slices" ? summary.categories_slices : summary.categories;
  const total = items.reduce((sum, item) => sum + item.count, 0);
  const { actions } = summary;
  const actionTotal = actions.continue + actions.stop + actions.wait;

  return (
    <>
      <h3 className="rp-subtitle">Смысл ответов</h3>
      <div className="rp-controls">
        <Switch
          label="Единица подсчёта"
          value={source}
          onChange={setSource}
          options={[
            { value: "dialogs", label: "По диалогам" },
            { value: "slices", label: "По срезам" },
          ]}
        />
        {hasLive && (
          <Switch
            label="Период режима"
            value={span}
            onChange={setSpan}
            options={[
              { value: "all", label: "Весь период" },
              { value: "before", label: "До 100%" },
              { value: "after", label: "После 100%" },
            ]}
          />
        )}
      </div>
      {total === 0 ? (
        <div className="empty-state">
          <p>Ответов пока нет</p>
        </div>
      ) : (
        <div className="rp-meaning">
          <CategoryDonut items={items} label="Смысл ответов покупателей" />
          <div>
            <h4>Что делаем дальше</h4>
            <Donut
              size={110}
              label="Продолжили, остановили, ждём"
              slices={[
                {
                  key: "continue",
                  label: "Продолжили",
                  count: actions.continue,
                  color: ACTION_COLOR.continue,
                },
                {
                  key: "stop",
                  label: "Остановили",
                  count: actions.stop,
                  color: ACTION_COLOR.stop,
                },
                {
                  key: "wait",
                  label: "Ждём",
                  count: actions.wait,
                  color: ACTION_COLOR.wait,
                },
              ]}
            />
            {actionTotal === 0 && <p className="muted">Решений пока нет.</p>}
          </div>
        </div>
      )}
    </>
  );
}

function ReplyDialogCard({
  sellerId,
  item,
  onClose,
}: {
  sellerId: string;
  item: EscalationItem;
  onClose: () => void;
}) {
  const dialog = useQuery({
    queryKey: [
      "support-bot-reply-dialog",
      sellerId,
      item.chat_id,
      item.prompt_at,
    ],
    queryFn: () => getReplyDialog(sellerId, item.chat_id, item.prompt_at),
  });
  const data = dialog.data;
  return (
    <section className="rp-dialog" aria-label="Карточка диалога">
      <div className="rp-dialog-head">
        <strong>Диалог от {stamp(item.prompt_at)}</strong>
        {(data?.chat_url ?? item.chat_url) && (
          <a
            className="wb-article-link"
            href={(data?.chat_url ?? item.chat_url) as string}
            target="_blank"
            rel="noreferrer"
          >
            Чат в WB <ExternalLink size={12} aria-hidden="true" />
          </a>
        )}
        <button type="button" className="secondary-button" onClick={onClose}>
          Скрыть
        </button>
      </div>
      {dialog.isLoading && (
        <div className="loading-block">Загружаем диалог…</div>
      )}
      {dialog.error && (
        <div className="inline-error">
          {message(dialog.error, "Не удалось загрузить диалог")}
        </div>
      )}
      {data && (
        <>
          <ol className="rp-messages">
            {data.messages.map((entry, at) => (
              <li key={at} className={`rp-msg rp-msg-${entry.role}`}>
                <span>
                  {ROLE_LABEL[entry.role]} · {stamp(entry.at)}
                </span>
                <p>{entry.text}</p>
              </li>
            ))}
            {data.messages.length === 0 && (
              <li className="muted">Сообщений нет.</li>
            )}
          </ol>
          <h4>Вердикты срезов</h4>
          <ul className="rp-verdicts">
            {data.verdicts.map((verdict, at) => (
              <li key={at}>
                <span>
                  {stamp(verdict.first_event_at)}–{stamp(verdict.last_event_at)}{" "}
                  · сообщений: {verdict.messages}
                </span>
                {verdict.category ? (
                  <Chip bucket={verdict.category} />
                ) : (
                  <em>{STATUS_LABEL[verdict.status]}</em>
                )}
                <span className="muted">
                  {STATUS_LABEL[verdict.status]}, {ACTION_LABEL[verdict.action]}
                  {verdict.reason ? ` — ${verdict.reason}` : ""}
                </span>
              </li>
            ))}
            {data.verdicts.length === 0 && (
              <li className="muted">Вердиктов нет.</li>
            )}
          </ul>
        </>
      )}
    </section>
  );
}

function Escalations({ sellerId }: { sellerId: string }) {
  const queryClient = useQueryClient();
  const [opened, setOpened] = useState<EscalationItem | null>(null);
  const [error, setError] = useState("");
  const list = useQuery({
    queryKey: ["support-bot-reply-escalations", sellerId],
    queryFn: () => getReplyEscalations(sellerId),
  });
  const close = useMutation({
    mutationFn: (item: EscalationItem) =>
      setDialogFlags(sellerId, item, { closed: true }),
    onSuccess: async () => {
      setError("");
      await queryClient.invalidateQueries({
        queryKey: ["support-bot-reply-escalations", sellerId],
      });
      await queryClient.invalidateQueries({
        queryKey: ["support-bot-reply-summary", sellerId],
      });
      await queryClient.invalidateQueries({
        queryKey: ["support-bot", sellerId],
      });
      await queryClient.invalidateQueries({
        queryKey: ["support-bot-dialogs", sellerId],
      });
    },
    onError: (e) => setError(message(e, "Не удалось закрыть диалог")),
  });
  const items = list.data?.items ?? [];

  return (
    <>
      <h3 className="rp-subtitle">Эскалации</h3>
      {error && <div className="inline-error">{error}</div>}
      {list.error && (
        <div className="inline-error">
          {message(list.error, "Не удалось загрузить эскалации")}
        </div>
      )}
      {list.isLoading ? (
        <div className="loading-block">Загружаем эскалации…</div>
      ) : (
        <section
          className="bot-journal-table rp-escalations"
          aria-label="Эскалации"
        >
          <div className="rp-esc-row bot-journal-head">
            <span>Класс</span>
            <span>Причина</span>
            <span>Когда</span>
            <span>Артикул</span>
            <span>Действия</span>
          </div>
          {items.map((item) => (
            <div
              className="rp-esc-row"
              key={`${item.chat_id}:${item.prompt_at}`}
            >
              <span>
                <Chip bucket={item.category} />
              </span>
              <span className="rp-reason" title={item.reason ?? undefined}>
                {item.reason ?? "—"}
              </span>
              <span>{stamp(item.event_at)}</span>
              <span>{item.nm_id ?? "—"}</span>
              <span className="rp-actions">
                {item.chat_url && (
                  <a
                    className="wb-article-link"
                    href={item.chat_url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Чат в WB <ExternalLink size={12} aria-hidden="true" />
                  </a>
                )}
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => setOpened(item)}
                >
                  Текст
                </button>
                <button
                  type="button"
                  className="secondary-button"
                  disabled={close.isPending || item.closed}
                  onClick={() => close.mutate(item)}
                >
                  {item.closed ? "Закрыт" : "Закрыть"}
                </button>
              </span>
            </div>
          ))}
          {items.length === 0 && (
            <span className="fbs-row-empty">
              Эскалаций без ответа менеджера нет.
            </span>
          )}
        </section>
      )}
      {opened && (
        <ReplyDialogCard
          sellerId={sellerId}
          item={opened}
          onClose={() => setOpened(null)}
        />
      )}
    </>
  );
}

export function SupportBotReplies({
  sellerId,
  gateMode,
}: {
  sellerId: string;
  gateMode: ReplyGateMode;
}) {
  const [days, setDays] = useState(14);
  const summary = useQuery({
    queryKey: ["support-bot-reply-summary", sellerId, days],
    queryFn: () => getReplySummary(sellerId, days),
    placeholderData: (previous) => previous,
  });
  const data = summary.data;
  const mode = data?.gate_mode ?? gateMode;
  const noReplies = data
    ? data.daily.every((day) => day.replies_live + day.replies_control === 0)
    : false;

  return (
    <div className="rp">
      <div className="rp-controls">
        <Switch
          label="Период"
          value={days}
          onChange={setDays}
          options={PERIODS.map((p) => ({ value: p.days, label: p.label }))}
        />
      </div>

      <div
        className={`checklist-notice${mode === "off" ? "" : " rp-mode-on"}`}
        role="status"
      >
        <strong>{GATE_NOTE[mode]}.</strong>
        {mode === "off" && " Слой выключен: классы не считаются."}
      </div>

      {summary.error && (
        <div className="inline-error">
          {message(summary.error, "Не удалось загрузить ответы")}
        </div>
      )}
      {summary.isLoading && (
        <div className="loading-block">Загружаем данные…</div>
      )}

      {data && (
        <>
          <h3 className="rp-subtitle">Эффект напоминаний</h3>
          <div className="rp-arms">
            <ArmColumn title="С напоминанием" arm={data.effect.live} />
            <ArmColumn
              title="Контроль (без реального напоминания)"
              arm={data.effect.control}
            />
          </div>
          <p className="muted rp-note">
            Контроль — диалоги, по которым настоящее напоминание не уходило
            (сухой прогон). После перехода на отправку по всем диалогам новых
            диалогов в контроле нет, доля ответивших сравнивается с периодом до
            перехода.
            {data.live_since && (
              <>
                {" "}
                Режим 100% с {dayFormatter.format(new Date(data.live_since))}.
              </>
            )}
          </p>

          <h3 className="rp-subtitle">Ответы по дням</h3>
          {noReplies ? (
            <div className="empty-state">
              <p>
                {mode === "off"
                  ? "Слой выключен: классы не считаются"
                  : "Ответов пока нет"}
              </p>
            </div>
          ) : (
            <>
              <div className="rp-key">
                <span>
                  <i style={{ background: "var(--accent)" }} />С напоминанием
                </span>
                <span>
                  <i style={{ background: "var(--line-strong)" }} />
                  Контроль
                </span>
                <span>
                  <i className="rp-dash" />
                  Накопленный итог (шкала справа)
                </span>
                {data.live_since && (
                  <span>
                    <i style={{ background: "var(--warning-mark)" }} />
                    Режим 100%
                  </span>
                )}
              </div>
              <DailyBars daily={data.daily} liveSince={data.live_since} />
              <h4 className="rp-chart-title">
                Доля ответивших за 48 ч по дню старта
              </h4>
              <RateLines daily={data.daily} liveSince={data.live_since} />
            </>
          )}

          <Meaning summary={data} />
          <Tiles summary={data} />
        </>
      )}

      <Escalations sellerId={sellerId} />
    </div>
  );
}
