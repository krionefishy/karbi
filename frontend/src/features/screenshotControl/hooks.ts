import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../../api/http";
import { uploadSubmission } from "./api";
import {
  addFiles,
  clearFinished,
  markItems,
  nextToStart,
  removeItem,
  retryItem,
  setAllOutcomes,
  setItemOutcome,
  submitDrafts,
  type UploadItem,
  type UploadLimits,
} from "./queue";
import type { Outcome } from "./types";

const newId = () => crypto.randomUUID();

export function uploadErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message || "Сервер не принял файл";
  return "Нет связи с сервером";
}

/** Очередь загрузки: не больше трёх файлов одновременно, повтор с тем же client_submission_id. */
export function useUploadQueue(limits: UploadLimits | undefined, onUploaded: () => void) {
  const [items, setItems] = useState<UploadItem[]>([]);
  const started = useRef(new Set<string>());
  const uploaded = useRef(onUploaded);
  uploaded.current = onUploaded;

  useEffect(() => {
    const next = nextToStart(items).filter((item) => !started.current.has(item.key));
    if (next.length === 0) return;
    next.forEach((item) => started.current.add(item.key));
    setItems((current) =>
      markItems(
        current,
        next.map((item) => item.key),
        { state: "uploading" },
      ),
    );
    for (const item of next) {
      uploadSubmission({ file: item.file, clientSubmissionId: item.clientId, outcome: item.outcome })
        .then(() => {
          setItems((current) => markItems(current, [item.key], { state: "done", error: "" }));
          uploaded.current();
        })
        .catch((error: unknown) => {
          setItems((current) => markItems(current, [item.key], { state: "error", error: uploadErrorMessage(error) }));
        })
        .finally(() => started.current.delete(item.key));
    }
  }, [items]);

  return {
    items,
    add: useCallback(
      (files: File[], outcome: Outcome | null) =>
        setItems((current) => addFiles(current, files, outcome, newId, limits)),
      [limits],
    ),
    setOutcome: useCallback(
      (key: string, outcome: Outcome) => setItems((current) => setItemOutcome(current, key, outcome)),
      [],
    ),
    setAll: useCallback((outcome: Outcome) => setItems((current) => setAllOutcomes(current, outcome)), []),
    submit: useCallback(() => setItems(submitDrafts), []),
    retry: useCallback((key: string) => setItems((current) => retryItem(current, key)), []),
    remove: useCallback((key: string) => setItems((current) => removeItem(current, key)), []),
    clear: useCallback(() => setItems(clearFinished), []),
  };
}

/** Миллисекунды до момента; обновляется раз в секунду, пока не дойдёт до нуля. */
export function useCountdown(until: string | null | undefined): number {
  const target = until ? new Date(until).getTime() : 0;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!target) return;
    setNow(Date.now());
    const timer = setInterval(() => {
      const current = Date.now();
      setNow(current);
      if (current >= target) clearInterval(timer);
    }, 1000);
    return () => clearInterval(timer);
  }, [target]);
  return target ? Math.max(0, target - now) : 0;
}
