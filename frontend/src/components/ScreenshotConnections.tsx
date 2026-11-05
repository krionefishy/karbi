import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, KeyRound } from "lucide-react";
import { useState } from "react";

import { createPairingCode, getInstalls, revokeInstall } from "../features/screenshotControl/api";
import { apiErrorText } from "../features/screenshotControl/errors";
import { formatCountdown } from "../features/screenshotControl/format";
import { useCountdown } from "../features/screenshotControl/hooks";
import type { ManagerConfig, PairingCode } from "../features/screenshotControl/types";
import { ScreenshotTelegram } from "./ScreenshotTelegram";
import { ScreenshotTimezone } from "./ScreenshotTimezone";

const INSTALLS_KEY = "sc-installs";
const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });
const stamp = (value: string | null) => (value ? momentFormatter.format(new Date(value)) : "—");

export function ScreenshotConnections({ config }: { config: ManagerConfig | undefined }) {
  return (
    <div className="sc-connections">
      <TelegramHowTo />
      <ExtensionPanel config={config} />
      <ScreenshotTimezone />
      <ScreenshotTelegram />
      <section className="sc-panel sc-connection">
        <h2>Как делать скриншот</h2>
        <ul className="sc-rules">
          <li>Снимайте окно браузера целиком: с адресной строкой и панелью чата.</li>
          <li>Не фотографируйте экран телефоном: такие снимки не засчитываются.</li>
          <li>С телефона — нативный скриншот приложения WB Partners, без обрезки.</li>
          <li>Один кейс — один скриншот. Повторная загрузка того же кейса уйдёт в дубли.</li>
        </ul>
      </section>
    </div>
  );
}

/** Инструкция менеджеру: как подключить телефон к боту и что он получит в ответ. */
function TelegramHowTo() {
  return (
    <details className="sc-help">
      <summary>Как присылать скриншоты с телефона</summary>
      <div className="sc-help-body">
        <p>
          Скриншоты принимаются двумя способами: расширением в Chrome на компьютере и Telegram-ботом с телефона.
          Всё присланное попадает в один список на вкладке «Мои скриншоты».
        </p>

        <h3>Шаг 1. Привязать Telegram</h3>
        <ol>
          <li>На этой вкладке в блоке «Telegram» нажмите «Привязать Telegram».</li>
          <li>
            Откройте полученную ссылку на телефоне — кнопкой «Открыть в Telegram», если вы на телефоне, или
            скопируйте её и перешлите себе. Ссылка одноразовая и живёт 15 минут; если не успели, получите новую.
          </li>
          <li>В открывшемся чате с ботом нажмите «Start». На этой странице появится ваш аккаунт — привязка готова.</li>
        </ol>

        <h3>Шаг 2. Прислать скриншот</h3>
        <ol>
          <li>
            Снимите нативный скриншот чата в приложении WB Partners: целиком, без обрезки, с панелью отзыва.
            Фото экрана другим телефоном не засчитывается.
          </li>
          <li>Отправьте снимок боту как фото.</li>
          <li>Под скриншотом выберите исход кнопкой: «Полож», «Доп» или «Удален».</li>
        </ol>

        <h3>Шаг 3. Что ответит бот</h3>
        <ul>
          <li>Скриншот засчитан — он уже в вашем списке и в отчёте за период.</li>
          <li>Не засчитан — бот напишет причину: не читается, обрезан, фото экрана или такой кейс уже присылали.</li>
          <li>
            Если причина в качестве снимка, переснимите нативным скриншотом целиком и пришлите заново. Если кейс
            действительно новый, он засчитается.
          </li>
        </ul>

        <p>
          Один кейс — один скриншот: повторная отправка того же отзыва уйдёт в дубли. Отвязать телефон можно в
          любой момент кнопкой «Отвязать» — присланное раньше останется на месте.
        </p>
      </div>
    </details>
  );
}

function ExtensionPanel({ config }: { config: ManagerConfig | undefined }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState("");
  const installs = useQuery({ queryKey: [INSTALLS_KEY], queryFn: getInstalls });
  const pairing = useMutation({
    mutationFn: createPairingCode,
    onSuccess: () => setError(""),
    onError: (err) => setError(apiErrorText(err, "Не удалось выпустить код")),
  });
  const revoke = useMutation({
    mutationFn: revokeInstall,
    onSuccess: () => {
      setError("");
      return queryClient.invalidateQueries({ queryKey: [INSTALLS_KEY] });
    },
    onError: (err) => setError(apiErrorText(err, "Не удалось отозвать установку")),
  });

  return (
    <section className="sc-panel sc-connection">
      <h2>Расширение Chrome</h2>
      {error && (
        <div className="inline-error" role="alert">
          {error}
        </div>
      )}
      <ol className="returns-steps">
        <li>
          {config?.extension_url ? (
            <a className="secondary-button" href={config.extension_url} download>
              <Download size={15} />
              Скачать расширение
            </a>
          ) : null}
          <span>Распакуйте архив в постоянную папку: удалять её после установки нельзя.</span>
        </li>
        <li>
          <span>
            Откройте <code>chrome://extensions</code>, включите «Режим разработчика», нажмите «Загрузить распакованное
            расширение» и выберите папку.
          </span>
        </li>
        <li>
          <button type="button" className="primary-button" disabled={pairing.isPending} onClick={() => pairing.mutate()}>
            <KeyRound size={15} aria-hidden="true" />
            {pairing.isPending ? "Готовим…" : "Получить код"}
          </button>
          <span>Введите код в попапе расширения. Один код — одна установка.</span>
        </li>
      </ol>
      {pairing.data && <PairingBanner code={pairing.data} />}

      <h3 className="sc-installs-title">Установки</h3>
      {installs.isLoading ? (
        <p className="muted">Загружаем установки…</p>
      ) : installs.isError ? (
        <div className="inline-error" role="alert">
          <div>
            <strong>Не удалось загрузить установки</strong>
            {apiErrorText(installs.error)}{" "}
            <button type="button" className="secondary-button docs-retry" onClick={() => void installs.refetch()}>
              Повторить
            </button>
          </div>
        </div>
      ) : !installs.data || installs.data.length === 0 ? (
        <p className="muted">Расширение ещё не подключено ни в одном браузере.</p>
      ) : (
        <table className="registry-table sc-installs">
          <thead>
            <tr>
              <th>Браузер</th>
              <th>Версия</th>
              <th>Подключено</th>
              <th>На связи</th>
              <th aria-label="Действия" />
            </tr>
          </thead>
          <tbody>
            {installs.data.map((install) => (
              <tr key={install.id}>
                <td>
                  <strong>{install.browser || "Браузер"}</strong>
                </td>
                <td className="mono">{install.ext_version ?? "—"}</td>
                <td>{stamp(install.created_at)}</td>
                <td>{stamp(install.last_seen_at)}</td>
                <td className="sc-installs-action">
                  <button
                    type="button"
                    className="secondary-button sc-small-button"
                    aria-label={`Отозвать установку: ${install.browser || "браузер"}`}
                    title="Токен этой установки перестанет работать"
                    disabled={revoke.isPending}
                    onClick={() => revoke.mutate(install.id)}
                  >
                    Отозвать
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function PairingBanner({ code }: { code: PairingCode }) {
  const left = useCountdown(code.expires_at);
  const expired = left <= 0;
  return (
    <div className={`invite-banner sc-pairing${expired ? " sc-pairing-expired" : ""}`}>
      <div>
        <span className="eyebrow">Код для расширения</span>
        <code className="returns-pairing-code">{code.code}</code>
        <em role="timer">{expired ? "Код истёк. Получите новый." : `Действует ещё ${formatCountdown(left)}`}</em>
      </div>
    </div>
  );
}
