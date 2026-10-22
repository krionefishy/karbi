import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, KeyRound } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api/http";
import { createPairingCode, getInstalls, revokeInstall } from "../features/screenshotControl/api";
import { formatCountdown } from "../features/screenshotControl/format";
import { useCountdown } from "../features/screenshotControl/hooks";
import type { ManagerConfig, PairingCode } from "../features/screenshotControl/types";

const INSTALLS_KEY = "sc-installs";
const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });
const stamp = (value: string | null) => (value ? momentFormatter.format(new Date(value)) : "—");

export function ScreenshotConnections({ config }: { config: ManagerConfig | undefined }) {
  return (
    <div className="sc-connections">
      <ExtensionPanel config={config} />
      <section className="sc-panel sc-connection">
        <div className="sc-connection-head">
          <h2>Telegram</h2>
          <span className="status-badge">Скоро</span>
        </div>
        <p className="muted">
          Отправка скриншотов боту появится в следующем релизе. Пока присылайте их через расширение или с этой страницы.
        </p>
      </section>
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

function ExtensionPanel({ config }: { config: ManagerConfig | undefined }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState("");
  const installs = useQuery({ queryKey: [INSTALLS_KEY], queryFn: getInstalls });
  const pairing = useMutation({
    mutationFn: createPairingCode,
    onSuccess: () => setError(""),
    onError: (err) => setError(err instanceof ApiError ? err.message : "Не удалось выпустить код"),
  });
  const revoke = useMutation({
    mutationFn: revokeInstall,
    onSuccess: () => {
      setError("");
      return queryClient.invalidateQueries({ queryKey: [INSTALLS_KEY] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "Не удалось отозвать установку"),
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
          <button className="primary-button" disabled={pairing.isPending} onClick={() => pairing.mutate()}>
            <KeyRound size={15} />
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
            {installs.error instanceof ApiError ? installs.error.message : "Сервер не ответил."}{" "}
            <button className="secondary-button docs-retry" onClick={() => void installs.refetch()}>
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
                    className="secondary-button sc-small-button"
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
