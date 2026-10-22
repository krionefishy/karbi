// Очередь загрузки скриншотов — чистые переходы без React. Хук-исполнитель — в hooks.ts.

import { formatBytes } from "./format";
import type { ManagerConfig, Outcome } from "./types";

export const UPLOAD_PARALLEL = 3;

export type UploadState = "draft" | "queued" | "uploading" | "done" | "error";

export interface UploadItem {
  key: string;
  file: File;
  /** Один на файл: повтор после ошибки шлёт тот же, сервер узнаёт повтор. */
  clientId: string;
  outcome: Outcome | null;
  state: UploadState;
  error: string;
  /** Ошибка проверки на месте: такой файл не отправляем вовсе. */
  invalid: boolean;
}

export type UploadLimits = Pick<ManagerConfig, "max_upload_bytes" | "accepted_types">;

const TYPE_NAMES: Record<string, string> = { "image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP" };

/** Проверка до отправки: тип и размер из конфига модуля. Пустая строка — файл годится. */
export function fileProblem(file: File, limits?: UploadLimits): string {
  if (!limits) return "";
  if (limits.accepted_types.length > 0 && !limits.accepted_types.includes(file.type)) {
    const names = limits.accepted_types.map((type) => TYPE_NAMES[type] ?? type).join(", ");
    return `Нужна картинка: ${names}`;
  }
  if (file.size > limits.max_upload_bytes) return `Файл больше ${formatBytes(limits.max_upload_bytes)}`;
  return "";
}

export function addFiles(
  items: UploadItem[],
  files: File[],
  outcome: Outcome | null,
  makeId: () => string,
  limits?: UploadLimits,
): UploadItem[] {
  const added = files.map((file): UploadItem => {
    const problem = fileProblem(file, limits);
    const clientId = makeId();
    return {
      key: clientId,
      file,
      clientId,
      outcome,
      state: problem ? "error" : "draft",
      error: problem,
      invalid: Boolean(problem),
    };
  });
  return [...items, ...added];
}

const editable = (item: UploadItem) => item.state === "draft" || (item.state === "error" && !item.invalid);

/** Исход одному файлу; ошибку «выберите исход» снимаем сразу. */
export function setItemOutcome(items: UploadItem[], key: string, outcome: Outcome): UploadItem[] {
  return items.map((item) =>
    item.key === key && editable(item) ? { ...item, outcome, error: item.state === "draft" ? "" : item.error } : item,
  );
}

/** Массовое «для всех»: всем ещё не отправленным. */
export function setAllOutcomes(items: UploadItem[], outcome: Outcome): UploadItem[] {
  return items.map((item) =>
    editable(item) ? { ...item, outcome, error: item.state === "draft" ? "" : item.error } : item,
  );
}

/** «Отправить»: черновики с исходом — в очередь, без исхода — ошибка рядом с файлом. */
export function submitDrafts(items: UploadItem[]): UploadItem[] {
  return items.map((item) => {
    if (item.state !== "draft") return item;
    return item.outcome ? { ...item, state: "queued", error: "" } : { ...item, error: "Выберите исход" };
  });
}

/** Повтор после ошибки сети или сервера: тот же clientId, снова в очередь. */
export function retryItem(items: UploadItem[], key: string): UploadItem[] {
  return items.map((item) =>
    item.key === key && item.state === "error" && !item.invalid && item.outcome
      ? { ...item, state: "queued", error: "" }
      : item,
  );
}

/** Какие файлы запускать сейчас, чтобы одновременно шло не больше limit. */
export function nextToStart(items: UploadItem[], limit = UPLOAD_PARALLEL): UploadItem[] {
  const running = items.filter((item) => item.state === "uploading").length;
  return items.filter((item) => item.state === "queued").slice(0, Math.max(0, limit - running));
}

export function markItems(items: UploadItem[], keys: string[], patch: Partial<UploadItem>): UploadItem[] {
  return items.map((item) => (keys.includes(item.key) ? { ...item, ...patch } : item));
}

export const removeItem = (items: UploadItem[], key: string) => items.filter((item) => item.key !== key);

export const clearFinished = (items: UploadItem[]) => items.filter((item) => item.state !== "done");

export interface QueueProgress {
  total: number;
  done: number;
  active: number;
  failed: number;
  drafts: number;
}

export function queueProgress(items: UploadItem[]): QueueProgress {
  const count = (states: UploadState[]) => items.filter((item) => states.includes(item.state)).length;
  return {
    total: items.length,
    done: count(["done"]),
    active: count(["queued", "uploading"]),
    failed: count(["error"]),
    drafts: count(["draft"]),
  };
}
