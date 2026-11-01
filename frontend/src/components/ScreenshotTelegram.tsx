import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Send } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";

import { createTelegramLink, getTelegramStatus, unlinkTelegram } from "../features/screenshotControl/api";
import { apiErrorText, isErrorCode } from "../features/screenshotControl/errors";
import { formatCountdown } from "../features/screenshotControl/format";
import { useCountdown } from "../features/screenshotControl/hooks";
import type { ManagerConfig, TelegramLink, TelegramStatus } from "../features/screenshotControl/types";
import { ConfirmDialog } from "./SellerDialog";

export const TELEGRAM_KEY = "sc-telegram";
/** Пока ссылка жива, спрашиваем статус привязки раз в 4 секунды. */
export const TELEGRAM_POLL_MS = 4000;

const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });
const handle = (username: string | null) => (username ? `@${username}` : "Telegram");

/** Блок «Telegram» во вкладке «Подключения»: привязка аккаунта к боту по одноразовой ссылке. */
export function ScreenshotTelegram() {
  return <TelegramPanel />;
}

function TelegramUnavailable() {
  return (
    <section className="sc-panel sc-connection">
      <div className="sc-connection-head">
        <h2>Telegram</h2>
        <span className="status-badge">Не подключён</span>
      </div>
      <p className="muted">
        Бот ещё не подключён администратором. Пока присылайте скриншоты через расширение или с этой страницы.
      </p>
    </section>
  );
}

function TelegramPanel() {
  const queryClient = useQueryClient();
  const [link, setLink] = useState<TelegramLink | null>(null);
  const [linkedNow, setLinkedNow] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<{ text: string; retry: (() => void) | null } | null>(null);
  const left = useCountdown(link?.expires_at);
  const waiting = Boolean(link) && left > 0;

  const status = useQuery({
    queryKey: [TELEGRAM_KEY],
    queryFn: getTelegramStatus,
    refetchInterval: (query) => (waiting && !query.state.data?.linked ? TELEGRAM_POLL_MS : false),
  });
  const linked = status.data?.linked ?? false;
  const botUsername = status.data?.bot_username ?? null;

  const issue = useMutation({
    mutationFn: createTelegramLink,
    onSuccess: (answer) => {
      setError(null);
      setLinkedNow(false);
      setLink(answer);
    },
    onError: (err) =>
      setError({
        text: apiErrorText(err, "Не удалось получить ссылку"),
        retry: isErrorCode(err, "telegram_unavailable") ? null : () => issue.mutate(),
      }),
  });
  const unlink = useMutation({
    mutationFn: unlinkTelegram,
    onSuccess: () => {
      setError(null);
      setConfirming(false);
      setLinkedNow(false);
      setLink(null);
      queryClient.setQueryData<TelegramStatus>([TELEGRAM_KEY], (current) => ({
        available: current?.available ?? true,
        bot_username: current?.bot_username ?? null,
        linked: false,
        username: null,
        linked_at: null,
      }));
    },
    onError: (err) => {
      setConfirming(false);
      setError({ text: apiErrorText(err, "Не удалось отвязать Telegram"), retry: null });
    },
  });

  // Бот принял ссылку: ссылка больше не нужна, показываем, кто привязан.
  useEffect(() => {
    if (link && linked) {
      setLink(null);
      setLinkedNow(true);
    }
  }, [link, linked]);

  let body: ReactNode;
  if (status.isError && !status.data) {
    body = (
      <div className="inline-error" role="alert">
        <div>
          <strong>Не удалось загрузить статус Telegram</strong>
          {apiErrorText(status.error)}{" "}
          <button type="button" className="secondary-button docs-retry" onClick={() => void status.refetch()}>
            Повторить
          </button>
        </div>
      </div>
    );
  } else if (!status.data) {
    body = <p className="muted">Загружаем статус Telegram…</p>;
  } else if (!status.data.available) {
    return <TelegramUnavailable />;
  } else if (linked) {
    const since = status.data.linked_at ? `, привязан ${momentFormatter.format(new Date(status.data.linked_at))}` : "";
    body = (
      <>
        {linkedNow && (
          <p className="sc-tg-done" role="status">
            Telegram привязан: {handle(status.data.username)}
          </p>
        )}
        <div className="sc-tg-linked">
          <span>
            <strong>{handle(status.data.username)}</strong>
            {since}
          </span>
          <button type="button" className="secondary-button sc-small-button" onClick={() => setConfirming(true)}>
            Отвязать
          </button>
        </div>
      </>
    );
  } else {
    body = (
      <>
        <p className="muted">Привяжите Telegram, чтобы присылать скриншоты боту прямо с телефона.</p>
        {link ? (
          <LinkBanner link={link} left={left} pending={issue.isPending} onRenew={() => issue.mutate()} />
        ) : (
          <button
            type="button"
            className="primary-button sc-tg-start"
            disabled={issue.isPending}
            onClick={() => issue.mutate()}
          >
            <Send size={15} aria-hidden="true" />
            {issue.isPending ? "Готовим ссылку…" : "Привязать Telegram"}
          </button>
        )}
      </>
    );
  }

  return (
    <section className="sc-panel sc-connection" aria-labelledby="sc-telegram-title">
      <h2 id="sc-telegram-title">Telegram</h2>
      {error && (
        <div className="inline-error sc-tg-error" role="alert">
          <div>
            {error.text}
            {error.retry && (
              <>
                {" "}
                <button type="button" className="secondary-button docs-retry" onClick={error.retry}>
                  Повторить
                </button>
              </>
            )}
          </div>
        </div>
      )}
      {body}

      <h3 className="sc-tg-howto-title">Как отправлять скриншоты в бот</h3>
      <ul className="sc-rules">
        <li>
          Присылайте {botUsername ? `боту @${botUsername}` : "боту"} нативный скриншот чата из приложения WB Partners,
          без обрезки.
        </li>
        <li>Под скриншотом выберите исход кнопкой: Полож, Доп или Удален.</li>
        <li>Бот ответит вердиктом: засчитан скриншот или нет и почему.</li>
      </ul>

      {confirming && (
        <ConfirmDialog
          danger
          title="Отвязать Telegram?"
          description="Бот перестанет принимать от вас скриншоты. Уже присланные останутся в списке. Привязать снова можно в любой момент."
          confirmLabel="Отвязать"
          pendingLabel="Отвязываем…"
          pending={unlink.isPending}
          onClose={() => setConfirming(false)}
          onConfirm={() => unlink.mutate()}
        />
      )}
    </section>
  );
}

function LinkBanner({
  link,
  left,
  pending,
  onRenew,
}: {
  link: TelegramLink;
  left: number;
  pending: boolean;
  onRenew: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const expired = left <= 0;

  const copy = () => {
    void navigator.clipboard
      ?.writeText(link.url)
      .then(() => setCopied(true))
      .catch(() => setCopied(false));
  };

  if (expired) {
    return (
      <div className="invite-banner sc-pairing sc-pairing-expired sc-tg-link">
        <div>
          <span className="eyebrow">Ссылка для Telegram</span>
          <em role="timer">Ссылка истекла.</em>
        </div>
        <button type="button" className="primary-button" disabled={pending} onClick={onRenew}>
          {pending ? "Готовим ссылку…" : "Получить новую ссылку"}
        </button>
      </div>
    );
  }

  return (
    <div className="invite-banner sc-pairing sc-tg-link">
      <div>
        <span className="eyebrow">Ссылка для Telegram</span>
        <code>{link.url}</code>
        <em role="timer">Действует ещё {formatCountdown(left)}</em>
        <p className="sc-tg-hint">
          Откройте ссылку в Telegram и нажмите Start. Ссылка действует 15 минут и одноразовая.
        </p>
      </div>
      <div className="sc-tg-actions">
        <a className="primary-button" href={link.url} target="_blank" rel="noreferrer">
          <Send size={15} aria-hidden="true" />
          Открыть в Telegram
        </a>
        <button type="button" className="secondary-button" onClick={copy}>
          <Copy size={15} aria-hidden="true" />
          {copied ? "Скопировано" : "Скопировать ссылку"}
        </button>
      </div>
    </div>
  );
}
