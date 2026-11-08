import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, ExternalLink, Lock } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api/http";
import {
  getProductDialogs,
  getProducts,
  setDialogFlags,
  setDialogSwitch,
  setProductSwitch,
} from "../features/supportBot/api";
import { mailingNote, productCounts, replyLabel, STATE_LABEL, verdictNote } from "../features/supportBot/format";
import type { BotDialog, DialogScope, ProductGroup } from "../features/supportBot/types";

const stampFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });
const stamp = (value: string | null) => (value ? stampFormatter.format(new Date(value)) : "—");

const SCOPES: { value: DialogScope; label: string }[] = [
  { value: "campaign", label: "Рассылка идёт или ждёт начала" },
  { value: "all", label: "Все диалоги" },
];

interface Props {
  sellerId: string;
  onError: (message: string) => void;
}

function Switch({
  on,
  disabled,
  label,
  large,
  onToggle,
}: {
  on: boolean;
  disabled?: boolean;
  label: string;
  large?: boolean;
  onToggle: () => void;
}) {
  const classes = ["bot-switch", on ? "bot-switch-on" : "", large ? "bot-switch-large" : ""].filter(Boolean).join(" ");
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      className={classes}
      onClick={(event) => {
        event.stopPropagation();
        onToggle();
      }}
    />
  );
}

function ProductDialogs({
  sellerId,
  group,
  scope,
  onToggle,
  onClose,
  busy,
}: {
  sellerId: string;
  group: ProductGroup;
  scope: DialogScope;
  onToggle: (dialog: BotDialog, on: boolean | null) => void;
  onClose: (dialog: BotDialog) => void;
  busy: boolean;
}) {
  const query = useInfiniteQuery({
    queryKey: ["support-bot-product-dialogs", sellerId, group.key, scope],
    queryFn: ({ pageParam }) => getProductDialogs(sellerId, group.key, scope, pageParam),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.page * last.page_size < last.total ? last.page + 1 : undefined),
  });
  const dialogs = query.data?.pages.flatMap((page) => page.dialogs) ?? [];

  if (query.isLoading) return <p className="muted bot-prod-note">Загружаем диалоги…</p>;
  if (dialogs.length === 0) return <p className="muted bot-prod-note">Под этот набор диалогов нет.</p>;
  return (
    <div className="bot-prod-rows">
      {dialogs.map((dialog) => {
        const mailing = dialog.mailing;
        const verdict = verdictNote(dialog);
        return (
          <div className="bot-prod-row" key={`${dialog.chat_id}:${dialog.prompt_at}`}>
            <span className="bot-prod-switch">
              {mailing && (
                <Switch
                  on={mailing.on}
                  disabled={mailing.locked || busy}
                  label={mailing.on ? "Выключить рассылку по диалогу" : "Включить рассылку по диалогу"}
                  onToggle={() => onToggle(dialog, !mailing.on)}
                />
              )}
              {mailing?.locked && <Lock size={12} aria-label="Тумблер недоступен" />}
            </span>
            <span>
              {dialog.chat_url ? (
                <a className="wb-article-link" href={dialog.chat_url} target="_blank" rel="noreferrer">
                  {stamp(dialog.prompt_at)} <ExternalLink size={12} />
                </a>
              ) : (
                <span title={dialog.chat_id}>{stamp(dialog.prompt_at)}</span>
              )}
            </span>
            <span className="bot-prod-state">
              <strong>{STATE_LABEL[dialog.state]}</strong>
              {mailing && (
                <em className={mailing.source === "manual" ? "bot-manual" : undefined}>
                  {mailingNote(mailing)}
                  {mailing.source === "manual" && (
                    <button type="button" className="bot-link" disabled={busy} onClick={() => onToggle(dialog, null)}>
                      вернуть к решению модели
                    </button>
                  )}
                </em>
              )}
              {dialog.reply_category && <em>{replyLabel(dialog.reply_category)}</em>}
            </span>
            <span className="bot-prod-verdict">{verdict ?? "—"}</span>
            <span className="bot-prod-sends">
              {dialog.next_send_at && dialog.state === "in_campaign"
                ? `следующее ${stamp(dialog.next_send_at)}`
                : `отправлено ${dialog.sent_count}`}
            </span>
            <span className="bot-prod-menu">
              <button type="button" className="bot-link" disabled={busy} onClick={() => onClose(dialog)}>
                {dialog.closed ? "Открыть" : "Закрыть"}
              </button>
            </span>
          </div>
        );
      })}
      {query.hasNextPage && (
        <button
          type="button"
          className="secondary-button bot-prod-more"
          disabled={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {query.isFetchingNextPage ? "Загружаем…" : "Показать ещё"}
        </button>
      )}
    </div>
  );
}

export function SupportBotProducts({ sellerId, onError }: Props) {
  const queryClient = useQueryClient();
  const [scope, setScope] = useState<DialogScope>("campaign");
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  const products = useQuery({
    queryKey: ["support-bot-products", sellerId, scope],
    queryFn: () => getProducts(sellerId, scope),
    placeholderData: (previous) => previous,
  });

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ["support-bot-products", sellerId] });
    await queryClient.invalidateQueries({ queryKey: ["support-bot-product-dialogs", sellerId] });
    await queryClient.invalidateQueries({ queryKey: ["support-bot", sellerId] });
  };
  const failed = (fallback: string) => (error: unknown) =>
    onError(error instanceof ApiError ? error.message : fallback);

  const productMutation = useMutation({
    mutationFn: (item: { key: number; enabled: boolean }) => setProductSwitch(sellerId, item.key, item.enabled),
    onSuccess: refresh,
    onError: failed("Не удалось переключить товар"),
  });
  const dialogMutation = useMutation({
    mutationFn: (item: { dialog: BotDialog; on: boolean | null }) => setDialogSwitch(sellerId, item.dialog, item.on),
    onSuccess: refresh,
    onError: failed("Не удалось переключить диалог"),
  });
  const closeMutation = useMutation({
    mutationFn: (dialog: BotDialog) => setDialogFlags(sellerId, dialog, { closed: !dialog.closed }),
    onSuccess: refresh,
    onError: failed("Не удалось изменить диалог"),
  });
  const busy = dialogMutation.isPending || closeMutation.isPending;

  const list = products.data?.products ?? [];
  const writing = list.reduce((sum, group) => sum + (group.enabled ? group.writing : 0), 0);
  const off = list.filter((group) => !group.enabled).length;
  const isOpen = (group: ProductGroup) => expanded[group.key] ?? (group.enabled && group.writing > 0);
  const hours = products.data?.manual_hours ?? 24;

  return (
    <section className="bot-prod" aria-label="Диалоги по товарам">
      <div className="checklist-toolbar penalties-toolbar">
        <div className="mode-switch penalties-groups" role="tablist" aria-label="Набор диалогов">
          {SCOPES.map((option) => (
            <button
              key={option.value}
              type="button"
              role="tab"
              aria-selected={scope === option.value}
              className={scope === option.value ? "mode-active" : undefined}
              onClick={() => setScope(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <span className="muted bot-prod-summary">
          Пишем сейчас по {writing} диалогам · выключено товаров: {off} из {list.length}
        </span>
      </div>
      <p className="muted chats-legend">
        Тумблер диалога повторяет решение модели. Если переключить его вручную, решение действует {hours} ч, потом
        тумблер снова следует за моделью. Выключенный товар не получает сообщений, а его диалоги не отправляются на
        оценку нейросети; выключенные товары стоят в конце списка.
      </p>
      {products.isLoading && <p className="muted">Загружаем товары…</p>}
      {!products.isLoading && list.length === 0 && <p className="muted">Диалогов по товарам пока нет.</p>}
      {list.map((group) => {
        const open = isOpen(group);
        return (
          <article key={group.key} className={group.enabled ? "bot-prod-group" : "bot-prod-group bot-prod-off"}>
            <div
              className="bot-prod-head"
              role="button"
              tabIndex={0}
              aria-expanded={open}
              onClick={() => setExpanded({ ...expanded, [group.key]: !open })}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setExpanded({ ...expanded, [group.key]: !open });
                }
              }}
            >
              <ChevronRight size={18} className={open ? "bot-chevron bot-chevron-open" : "bot-chevron"} />
              {group.photo_url ? (
                <img className="bot-prod-photo" src={group.photo_url} alt="" loading="lazy" />
              ) : (
                <span className="bot-prod-photo bot-prod-photo-empty">{group.name.slice(0, 1)}</span>
              )}
              <span className="bot-prod-title">
                <strong>{group.name}</strong>
                <em>
                  {group.nm_id ?? "WB не передал артикул"}
                  {group.vendor_code ? ` · ${group.vendor_code}` : ""}
                  {group.removed ? " · снят с продажи" : ""}
                </em>
              </span>
              <span className="bot-prod-counts">{productCounts(group)}</span>
              <Switch
                on={group.enabled}
                large
                disabled={productMutation.isPending}
                label={group.enabled ? `Выключить рассылку по товару ${group.name}` : `Включить рассылку по товару ${group.name}`}
                onToggle={() => productMutation.mutate({ key: group.key, enabled: !group.enabled })}
              />
            </div>
            {open && (
              <ProductDialogs
                sellerId={sellerId}
                group={group}
                scope={scope}
                busy={busy}
                onToggle={(dialog, on) => dialogMutation.mutate({ dialog, on })}
                onClose={(dialog) => closeMutation.mutate(dialog)}
              />
            )}
          </article>
        );
      })}
    </section>
  );
}
