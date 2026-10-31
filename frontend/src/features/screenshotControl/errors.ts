// Тексты ошибок API «Контроля скриншотов» по машинному коду (заголовок X-Error-Code).
// Где фраза по коду понятнее detail сервера в контексте экрана — она; иначе detail как есть.

import { ApiError } from "../../api/http";
import type { ErrorCode } from "./types";

const PHRASES: Partial<Record<ErrorCode, string>> = {
  transition_not_allowed: "Это решение для скриншота уже недоступно: его разобрали раньше. Обновите очередь.",
  bad_cursor: "Список изменился, пока вы его листали. Загрузите его заново.",
  note_required: "Без комментария это решение не принять: напишите причину.",
  exact_key_conflict: "Такой кейс уже засчитан у другого скриншота. Засчитать второй раз нельзя, можно отметить дублем.",
  duplicate_target_invalid: "Этот скриншот не подходит как оригинал для дубля.",
  outcome_locked: "Исход уже не поменять: решение по скриншоту принято.",
  rate_limited: "Слишком много запросов подряд. Подождите минуту и повторите.",
  limiter_unavailable: "Сервис временно не принимает запросы. Повторите через минуту.",
  module_off: "Контроль скриншотов сейчас выключен.",
  storage_full: "Хранилище скриншотов заполнено, приём временно остановлен. Сообщите администратору.",
  period_too_long: "Период слишком длинный: выберите короче.",
  bad_timezone: "Такого часового пояса нет в списке. Обновите страницу и выберите снова.",
};

/** Коды, при которых повтор с тем же файлом бесполезен: сервер его не примет и во второй раз. */
const FILE_REJECTED: ReadonlySet<string> = new Set<ErrorCode>([
  "file_too_large",
  "unsupported_type",
  "too_small",
  "bad_image",
  "bad_outcome",
  "outcome_required",
  "idempotency_conflict",
]);

export function isErrorCode(error: unknown, code: ErrorCode): boolean {
  return error instanceof ApiError && error.code === code;
}

/** Фраза для показа: по коду, иначе текст сервера, иначе запасная. Без ответа сервера — «нет связи». */
export function apiErrorText(error: unknown, fallback = "Сервер не ответил"): string {
  if (error instanceof ApiError) {
    const phrase = error.code ? PHRASES[error.code as ErrorCode] : undefined;
    return phrase ?? (error.message || fallback);
  }
  return "Нет связи с сервером";
}

/** Сервер отверг сам файл (413/415/422 и т.п.): кнопка «Повторить» не нужна. */
export function isFileRejected(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.code) return FILE_REJECTED.has(error.code);
  return error.status === 413 || error.status === 415 || error.status === 422;
}
