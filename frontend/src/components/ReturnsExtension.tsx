import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, KeyRound, Trash2 } from "lucide-react";
import { useState } from "react";

import { ApiError } from "../api/http";
import { createPairingCode, getExtensionState, revokeInstall } from "../features/returns/api";
import type { ExtensionInstall } from "../features/returns/types";

const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });
const timeFormatter = new Intl.DateTimeFormat("ru-RU", { timeStyle: "short" });

const STATE_TITLES: Record<ExtensionInstall["state"], string> = {
  ok: "Работает",
  needs_login: "Нужен вход на WB",
  error: "Ошибка",
};

function stamp(value: string | null): string {
  return value ? momentFormatter.format(new Date(value)) : "—";
}

/** Вкладка «Расширение»: скачать, получить код пары, следить за установками и кодом дня. */
export function ReturnsExtension({ sellerId }: { sellerId: string }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState("");
  const { data: state, isLoading } = useQuery({
    queryKey: ["wb-returns-extension", sellerId],
    queryFn: () => getExtensionState(sellerId),
    refetchInterval: 30_000,
  });
  const pairingMutation = useMutation({
    mutationFn: () => createPairingCode(sellerId),
    onSuccess: () => setError(""),
    onError: (err) => setError(err instanceof ApiError ? err.message : "Не удалось выпустить код"),
  });
  const revokeMutation = useMutation({
    mutationFn: (installId: string) => revokeInstall(sellerId, installId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["wb-returns-extension", sellerId] }),
    onError: (err) => setError(err instanceof ApiError ? err.message : "Не удалось отключить установку"),
  });

  if (isLoading || !state) {
    return <div className="loading-block">Загружаем данные…</div>;
  }
  const pairing = pairingMutation.data;

  return (
    <div className="returns-extension">
      {error && <div className="inline-error">{error}</div>}
      <section className="panel returns-extension-panel">
        <h2>Как подключить</h2>
        <ol className="returns-steps">
          <li>
            <a className="secondary-button" href={state.download_url} download>
              <Download size={15} />
              Скачать расширение
            </a>
            <span>Распакуйте архив на компьютере, где будет открыт профиль покупателя владельца кабинета.</span>
          </li>
          <li>
            <span>
              Откройте <code>chrome://extensions</code> (в Яндекс Браузере <code>browser://extensions</code>),
              включите «Режим разработчика», нажмите «Загрузить распакованное расширение» и выберите папку.
            </span>
          </li>
          <li>
            <span>В том же браузере войдите на wildberries.ru под телефоном владельца кабинета.</span>
          </li>
          <li>
            <button
              className="primary-button"
              disabled={pairingMutation.isPending}
              onClick={() => pairingMutation.mutate()}
            >
              <KeyRound size={15} />
              {pairingMutation.isPending ? "Готовим…" : "Получить код для расширения"}
            </button>
            <span>
              Введите код в настройках расширения. Тот же код выдаёт бот по команде /extension. Адрес сервиса
              там уже заполнен; если поле пустое — впишите <code>{window.location.origin}</code>.
            </span>
          </li>
        </ol>
        {pairing && (
          <div className="invite-banner">
            <div>
              <span className="eyebrow">Код для расширения</span>
              <code className="returns-pairing-code">{pairing.code}</code>
              <em>Действует до {timeFormatter.format(new Date(pairing.expires_at))}. Один код — одна установка.</em>
            </div>
          </div>
        )}
      </section>

      <section className="panel returns-extension-panel">
        <div className="returns-extension-head">
          <h2>Код получения на {new Intl.DateTimeFormat("ru-RU", { dateStyle: "long" }).format(new Date(state.code_date))}</h2>
          <span className={`status-badge ${state.has_code_today ? "status-active" : "status-degraded"}`}>
            <span />
            {state.has_code_today ? `Получен ${stamp(state.code_received_at)}` : "Ещё не пришёл"}
          </span>
        </div>
        <p className="muted">
          {state.pending_tasks
            ? "Расширению отправлена просьба обновить код — заберёт при следующей проверке."
            : "Расширение присылает код после полуночи и в полдень по Москве, а также по команде /qr в боте."}
        </p>
      </section>

      <section className="panel returns-extension-panel">
        <h2>Установки</h2>
        {state.installs.length === 0 ? (
          <p className="muted">Расширение ещё не подключено ни в одном браузере.</p>
        ) : (
          <table className="registry-table">
            <thead>
              <tr>
                <th>Браузер</th>
                <th>Состояние</th>
                <th>На связи</th>
                <th>Код присылало</th>
                <th>Готово к выдаче в профиле</th>
                <th aria-label="Действия" />
              </tr>
            </thead>
            <tbody>
              {state.installs.map((install) => (
                <tr key={install.id}>
                  <td>
                    <strong>{install.browser || install.install_id}</strong>
                    <br />
                    <small className="muted">подключено {stamp(install.created_at)}</small>
                  </td>
                  <td>
                    <span className={`status-badge status-${install.state === "ok" ? "active" : install.state === "needs_login" ? "degraded" : "failed"}`}>
                      <span />
                      {STATE_TITLES[install.state]}
                    </span>
                    {install.last_error && (
                      <>
                        <br />
                        <small className="muted">{install.last_error}</small>
                      </>
                    )}
                  </td>
                  <td>{stamp(install.last_seen_at)}</td>
                  <td>{stamp(install.last_code_at)}</td>
                  <td>
                    {install.deliveries_at ? `${install.deliveries_count} · ${stamp(install.deliveries_at)}` : "—"}
                  </td>
                  <td>
                    <button
                      className="icon-button"
                      title="Отключить установку: её токен перестанет работать"
                      disabled={revokeMutation.isPending}
                      onClick={() => revokeMutation.mutate(install.id)}
                    >
                      <Trash2 size={15} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
