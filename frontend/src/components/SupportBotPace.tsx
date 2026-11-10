import { useState } from "react";

import { paceError, paceExample } from "../features/supportBot/format";
import type { Pace, PaceInput } from "../features/supportBot/types";

interface Props {
  pace: Pace;
  defaults: Pace;
  custom: boolean;
  busy: boolean;
  error: string;
  /** Сколько диалогов пересчитано последним сохранением; `null` — сохранения ещё не было. */
  rescheduled: number | null;
  onSave: (pace: PaceInput) => void;
}

type Field = keyof Pace;

const FIELDS: { key: Field; label: string; unit: string }[] = [
  { key: "interval_minutes", label: "Интервал, мин", unit: "interval" },
  { key: "jitter_min_minutes", label: "Добавка от, мин", unit: "from" },
  { key: "jitter_max_minutes", label: "Добавка до, мин", unit: "to" },
];

function draftOf(pace: Pace): Record<Field, string> {
  return {
    interval_minutes: String(pace.interval_minutes),
    jitter_min_minutes: String(pace.jitter_min_minutes),
    jitter_max_minutes: String(pace.jitter_max_minutes),
  };
}

function parse(draft: Record<Field, string>): Pace {
  const number = (value: string) => (value.trim() === "" ? Number.NaN : Number(value));
  return {
    interval_minutes: number(draft.interval_minutes),
    jitter_min_minutes: number(draft.jitter_min_minutes),
    jitter_max_minutes: number(draft.jitter_max_minutes),
  };
}

/** Шаг рассылки кабинета: интервал плюс случайная добавка; пустое значение берётся из общих настроек. */
export function SupportBotPace({ pace, defaults, custom, busy, error, rescheduled, onSave }: Props) {
  const [draft, setDraft] = useState(() => draftOf(pace));
  const parsed = parse(draft);
  const problem = paceError(parsed);
  const unchanged = FIELDS.every(({ key }) => parsed[key] === pace[key]);
  const isDefault = FIELDS.every(({ key }) => parsed[key] === defaults[key]);

  const update = (key: Field, value: string) => setDraft((current) => ({ ...current, [key]: value }));
  const reset = () => {
    setDraft(draftOf(defaults));
    onSave({ interval_minutes: null, jitter_min_minutes: null, jitter_max_minutes: null });
  };
  const save = () => {
    // Значение, равное общему, не фиксируем за кабинетом: общая настройка потом сменится и для него.
    const input = {} as PaceInput;
    for (const { key } of FIELDS) input[key] = parsed[key] === defaults[key] ? null : parsed[key];
    onSave(input);
  };

  return (
    <section className="bot-pace" aria-label="Интервал рассылки">
      <div className="bot-pace-head">
        <h2 className="chats-section-title">Интервал рассылки</h2>
        <span className="bot-pace-badge">{custom ? "Свои значения кабинета" : "Общие значения"}</span>
      </div>
      <p className="field-note bot-pace-lead">
        Следующее сообщение по диалогу уйдёт через интервал плюс случайная добавка
        {problem ? "." : ` (${paceExample(parsed)}).`}
      </p>
      <div className="bot-pace-fields">
        {FIELDS.map(({ key, label }) => (
          <label key={key} className="bot-pace-field">
            <span className="field-label">{label}</span>
            <input
              type="number"
              inputMode="numeric"
              min={0}
              step={1}
              value={draft[key]}
              disabled={busy}
              aria-label={label}
              onChange={(event) => update(key, event.target.value)}
            />
            <span className="field-note bot-pace-default">Общее: {defaults[key]}</span>
          </label>
        ))}
      </div>
      {(problem || error) && (
        <p className="form-error" role="alert">
          {problem ?? error}
        </p>
      )}
      <p className="field-note">
        При сохранении расписание уже идущих диалогов будет пересчитано от их последней отправки по новому шагу.
      </p>
      {rescheduled !== null && !error && !problem && unchanged && (
        <p className="field-note bot-pace-saved" role="status">
          Сохранено. Пересчитано диалогов: {rescheduled}.
        </p>
      )}
      <div className="bot-actions bot-pace-actions">
        <button type="button" className="primary-button" disabled={busy || unchanged || problem !== null} onClick={save}>
          Сохранить
        </button>
        <button type="button" className="secondary-button" disabled={busy || (!custom && isDefault)} onClick={reset}>
          Сбросить к общему
        </button>
      </div>
    </section>
  );
}
