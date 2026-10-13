import { Check, Pencil, Plus, Trash2, X } from "lucide-react";
import { useState } from "react";

import type { Template } from "../features/supportBot/types";

/** Потолок WB на текст одного сообщения. */
const MAX_LENGTH = 1000;

interface Props {
  templates: Template[];
  busy: boolean;
  onAdd: (text: string) => void;
  onSave: (id: number, text: string) => void;
  onToggle: (id: number, active: boolean) => void;
  onDelete: (id: number) => void;
}

function Counter({ value }: { value: string }) {
  const over = value.length > MAX_LENGTH;
  return (
    <span className={over ? "bot-counter bot-counter-over" : "bot-counter"}>
      {value.length} из {MAX_LENGTH}
    </span>
  );
}

function Editor({
  initial,
  busy,
  saveLabel,
  onSave,
  onCancel,
}: {
  initial: string;
  busy: boolean;
  saveLabel: string;
  onSave: (text: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const empty = value.trim().length === 0;
  const invalid = empty || value.length > MAX_LENGTH;
  return (
    <div className="bot-editor">
      <textarea
        autoFocus
        rows={4}
        value={value}
        aria-label="Текст напоминания"
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
          if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && !invalid) onSave(value);
        }}
      />
      <div className="bot-editor-foot">
        <Counter value={value} />
        <span className="bot-actions">
          <button type="button" className="secondary-button" onClick={onCancel}>
            <X size={14} /> Отмена
          </button>
          <button type="button" className="primary-button" disabled={busy || invalid} onClick={() => onSave(value)}>
            <Check size={14} /> {saveLabel}
          </button>
        </span>
      </div>
    </div>
  );
}

/** Вариант A: каждый текст — карточка с тумблером; правка по клику прямо в карточке. */
export function SupportBotTemplates({ templates, busy, onAdd, onSave, onToggle, onDelete }: Props) {
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [removing, setRemoving] = useState<number | null>(null);

  return (
    <div className="bot-templates">
      {templates.map((item) => (
        <article className={item.active ? "bot-template" : "bot-template bot-template-off"} key={item.id}>
          {editing === item.id ? (
            <Editor
              initial={item.text}
              busy={busy}
              saveLabel="Сохранить"
              onCancel={() => setEditing(null)}
              onSave={(text) => {
                onSave(item.id, text);
                setEditing(null);
              }}
            />
          ) : (
            <>
              <div className="bot-template-row">
                <button
                  type="button"
                  role="switch"
                  aria-checked={item.active}
                  aria-label={item.active ? "Отключить текст" : "Включить текст"}
                  className={item.active ? "bot-switch bot-switch-on" : "bot-switch"}
                  onClick={() => onToggle(item.id, !item.active)}
                />
                <button
                  type="button"
                  className="bot-template-text"
                  title="Нажмите, чтобы изменить"
                  onClick={() => setEditing(item.id)}
                >
                  {item.text}
                </button>
                <span className="bot-actions">
                  <button
                    type="button"
                    className="icon-button"
                    aria-label="Изменить текст"
                    onClick={() => setEditing(item.id)}
                  >
                    <Pencil size={15} />
                  </button>
                  <button
                    type="button"
                    className="icon-button"
                    aria-label="Удалить текст"
                    onClick={() => setRemoving(removing === item.id ? null : item.id)}
                  >
                    <Trash2 size={15} />
                  </button>
                </span>
              </div>
              <div className="bot-template-meta">
                {item.text.length} из {MAX_LENGTH}
                {!item.active && " · отключён: бот его не использует"}
              </div>
              {removing === item.id && (
                <div className="bot-confirm" role="alert">
                  Удалить этот текст?
                  <span className="bot-actions">
                    <button type="button" className="secondary-button" onClick={() => setRemoving(null)}>
                      Нет
                    </button>
                    <button
                      type="button"
                      className="danger-button"
                      onClick={() => {
                        onDelete(item.id);
                        setRemoving(null);
                      }}
                    >
                      Удалить
                    </button>
                  </span>
                </div>
              )}
            </>
          )}
        </article>
      ))}

      {editing === "new" ? (
        <article className="bot-template">
          <Editor
            initial=""
            busy={busy}
            saveLabel="Добавить"
            onCancel={() => setEditing(null)}
            onSave={(text) => {
              onAdd(text);
              setEditing(null);
            }}
          />
        </article>
      ) : (
        <button type="button" className="bot-add" onClick={() => setEditing("new")}>
          <Plus size={16} /> Добавить текст
        </button>
      )}
      {templates.length === 0 && editing !== "new" && (
        <p className="muted bot-empty">Текстов пока нет: бот не будет ничего отправлять, пока не добавите хотя бы один.</p>
      )}
    </div>
  );
}
