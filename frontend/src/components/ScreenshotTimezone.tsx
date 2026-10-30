import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { getSettings, updateSettings } from "../features/screenshotControl/api";
import { apiErrorText } from "../features/screenshotControl/errors";
import type { ManagerSettings } from "../features/screenshotControl/types";

export const SETTINGS_KEY = "sc-settings";

export function useManagerSettings() {
  return useQuery({ queryKey: [SETTINGS_KEY], queryFn: getSettings, staleTime: 5 * 60_000 });
}

/** Блок «Часовой пояс» во вкладке «Подключения»: в нём читается время отзыва на скриншотах менеджера. */
export function ScreenshotTimezone() {
  const queryClient = useQueryClient();
  const settings = useManagerSettings();
  const [choice, setChoice] = useState("");
  const [saved, setSaved] = useState(false);
  const save = useMutation({
    mutationFn: updateSettings,
    onSuccess: (answer: ManagerSettings) => {
      queryClient.setQueryData([SETTINGS_KEY], answer);
      setSaved(true);
    },
  });

  useEffect(() => {
    if (settings.data) setChoice(settings.data.timezone);
  }, [settings.data]);

  const current = settings.data?.timezone;
  const changed = Boolean(choice) && choice !== current;

  return (
    <section className="sc-panel sc-connection" aria-labelledby="sc-timezone-title">
      <h2 id="sc-timezone-title">Часовой пояс</h2>
      <p className="muted">Укажите пояс, в котором кабинет WB показывает вам время отзыва.</p>
      {settings.isError ? (
        <div className="inline-error" role="alert">
          <div>
            <strong>Не удалось загрузить часовой пояс</strong>
            {apiErrorText(settings.error)}{" "}
            <button type="button" className="secondary-button docs-retry" onClick={() => void settings.refetch()}>
              Повторить
            </button>
          </div>
        </div>
      ) : !settings.data ? (
        <p className="muted">Загружаем настройки…</p>
      ) : (
        <form
          className="sc-timezone"
          onSubmit={(event) => {
            event.preventDefault();
            if (changed) save.mutate(choice);
          }}
        >
          <label className="field-label" htmlFor="sc-timezone-select">
            Пояс
          </label>
          <div className="sc-timezone-row">
            <select
              id="sc-timezone-select"
              value={choice}
              disabled={save.isPending}
              onChange={(event) => {
                setChoice(event.target.value);
                setSaved(false);
                save.reset();
              }}
            >
              {settings.data.zones.map((zone) => (
                <option key={zone.id} value={zone.id}>
                  {zone.label}
                  {zone.id === settings.data.default ? " — по умолчанию" : ""}
                </option>
              ))}
            </select>
            <button type="submit" className="primary-button" disabled={!changed || save.isPending}>
              {save.isPending ? "Сохраняем…" : "Сохранить"}
            </button>
          </div>
          {save.isError ? (
            <p className="form-error" role="alert">
              {apiErrorText(save.error, "Не удалось сохранить пояс")}
            </p>
          ) : saved && !changed ? (
            <p className="sc-timezone-note" role="status">
              Сохранено. Новый пояс действует для следующих скриншотов, уже отправленные не пересчитываются.
            </p>
          ) : (
            <p className="sc-timezone-note">Смена пояса не пересчитывает уже отправленные скриншоты.</p>
          )}
        </form>
      )}
    </section>
  );
}
