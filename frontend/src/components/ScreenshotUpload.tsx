import { ImageUp, RotateCcw, X } from "lucide-react";
import { type DragEvent, useEffect, useRef, useState } from "react";

import { formatBytes, OUTCOMES, outcomeLabel, plural } from "../features/screenshotControl/format";
import { useUploadQueue } from "../features/screenshotControl/hooks";
import { queueProgress, type UploadItem, type UploadLimits } from "../features/screenshotControl/queue";
import type { Outcome } from "../features/screenshotControl/types";

const FILES: [string, string, string] = ["файл", "файла", "файлов"];

function imageFiles(list: FileList | null | undefined): File[] {
  return Array.from(list ?? []).filter((file) => file.type.startsWith("image/") || file.type === "");
}

interface ScreenshotUploadProps {
  limits: UploadLimits | undefined;
  onUploaded: () => void;
}

export function ScreenshotUpload({ limits, onUploaded }: ScreenshotUploadProps) {
  const queue = useUploadQueue(limits, onUploaded);
  const [bulk, setBulk] = useState<Outcome | null>(null);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const { add } = queue;

  useEffect(() => {
    const onPaste = (event: ClipboardEvent) => {
      const files = imageFiles(event.clipboardData?.files);
      if (files.length === 0) return;
      event.preventDefault();
      add(files, bulk);
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, [add, bulk]);

  const progress = queueProgress(queue.items);
  const missing = queue.items.filter((item) => item.state === "draft" && !item.outcome && item.error).length;

  const chooseAll = (outcome: Outcome) => {
    setBulk(outcome);
    queue.setAll(outcome);
  };
  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    const files = imageFiles(event.dataTransfer?.files);
    if (files.length > 0) queue.add(files, bulk);
  };

  return (
    <section className="sc-panel sc-upload" aria-label="Загрузка скриншотов">
      <div
        className={`sc-drop${dragging ? " sc-drop-active" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <span className="sc-drop-icon" aria-hidden="true">
          <ImageUp size={22} />
        </span>
        <strong>Перетащите скриншот</strong>
        <span className="muted">или вставьте из буфера, Ctrl+V</span>
        <button type="button" className="secondary-button" onClick={() => input.current?.click()}>
          Выбрать файлы
        </button>
        <input
          ref={input}
          type="file"
          multiple
          hidden
          aria-label="Выбрать файлы"
          accept={limits?.accepted_types.join(",") || "image/*"}
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            if (files.length > 0) queue.add(files, bulk);
            event.target.value = "";
          }}
        />
      </div>

      <div className="sc-outcome">
        <span className="field-label" id="sc-outcome-all">
          {queue.items.length > 1 ? "Исход для всех" : "Исход"}
        </span>
        <OutcomeSwitch labelledBy="sc-outcome-all" value={bulk} onChange={chooseAll} />
        {missing > 0 && (
          <p className="form-error" role="alert">
            {queue.items.length > 1
              ? `Выберите исход: ${missing} ${plural(missing, FILES)} без исхода`
              : "Выберите исход: Полож, Доп или Удален"}
          </p>
        )}
      </div>

      {queue.items.length > 0 && (
        <ul className="sc-queue" aria-label="Файлы к отправке">
          {queue.items.map((item) => (
            <QueueRow
              key={item.key}
              item={item}
              onOutcome={(outcome) => queue.setOutcome(item.key, outcome)}
              onRetry={() => queue.retry(item.key)}
              onRemove={() => queue.remove(item.key)}
            />
          ))}
        </ul>
      )}

      <div className="sc-upload-actions">
        <button type="button" className="primary-button" disabled={progress.drafts === 0} onClick={queue.submit}>
          Отправить
          {progress.drafts > 1 ? ` ${progress.drafts} ${plural(progress.drafts, FILES)}` : ""}
        </button>
        {progress.total > 0 && (
          <span className="sc-progress muted" role="status">
            Отправлено {progress.done} из {progress.total}
            {progress.failed > 0 ? `, с ошибкой ${progress.failed}` : ""}
          </span>
        )}
        {progress.done > 0 && progress.active === 0 && (
          <button type="button" className="ghost-button" onClick={queue.clear}>
            Убрать отправленные
          </button>
        )}
      </div>

      <ul className="sc-hints">
        <li>Расширение Chrome: Alt+Shift+S, исход выбирается в попапе.</li>
        <li>Скриншот окна целиком, не фото экрана; с телефона — нативный скриншот.</li>
      </ul>
    </section>
  );
}

function OutcomeSwitch({
  value,
  onChange,
  labelledBy,
  label,
  disabled = false,
}: {
  value: Outcome | null;
  onChange: (outcome: Outcome) => void;
  labelledBy?: string;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <div className="seg sc-outcome-seg" role="group" aria-labelledby={labelledBy} aria-label={label}>
      {OUTCOMES.map((outcome) => (
        <button
          key={outcome}
          type="button"
          className={value === outcome ? "seg-active" : undefined}
          aria-pressed={value === outcome}
          disabled={disabled}
          onClick={() => onChange(outcome)}
        >
          {outcomeLabel(outcome)}
        </button>
      ))}
    </div>
  );
}

const STATE_TEXT: Record<UploadItem["state"], string> = {
  draft: "Готов к отправке",
  queued: "В очереди",
  uploading: "Отправляем…",
  done: "Отправлен",
  error: "",
};

function QueueRow({
  item,
  onOutcome,
  onRetry,
  onRemove,
}: {
  item: UploadItem;
  onOutcome: (outcome: Outcome) => void;
  onRetry: () => void;
  onRemove: () => void;
}) {
  const locked = item.state === "queued" || item.state === "uploading" || item.state === "done" || item.invalid;
  return (
    <li className={`sc-queue-row sc-queue-${item.state}`}>
      <div className="sc-queue-file">
        <strong title={item.file.name}>{item.file.name || "Скриншот из буфера"}</strong>
        <small className={item.error ? "sc-queue-error" : "muted"}>
          {item.error || STATE_TEXT[item.state]} · {formatBytes(item.file.size)}
        </small>
      </div>
      {!item.invalid && (
        <OutcomeSwitch
          label={`Исход для ${item.file.name || "скриншота"}`}
          value={item.outcome}
          onChange={onOutcome}
          disabled={locked}
        />
      )}
      <div className="sc-queue-actions">
        {item.state === "error" && !item.invalid && (
          <button type="button" className="secondary-button sc-small-button" onClick={onRetry} disabled={!item.outcome}>
            <RotateCcw size={14} />
            Повторить
          </button>
        )}
        {(item.state === "draft" || item.state === "error") && (
          <button
            type="button"
            className="ghost-button icon-button small"
            aria-label={`Убрать ${item.file.name || "скриншот"}`}
            title="Убрать из списка"
            onClick={onRemove}
          >
            <X size={14} />
          </button>
        )}
      </div>
    </li>
  );
}
