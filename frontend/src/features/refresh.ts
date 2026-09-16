/** Состояние запроса «Обновить данные» — одинаковое у всех автоматизаций с кнопкой. */
export interface RefreshLike {
  status: "queued" | "running" | "success" | "error";
  in_progress: boolean;
  finished_at: string | null;
  error: string | null;
}

/**
 * Текст ошибки ручного обновления, которую ещё есть смысл показывать.
 *
 * Плановый сбор, прошедший после неудачного нажатия, делает её историей: данные на
 * странице уже свежее, чем та ошибка, и «Обновление не удалось» под ними только пугает.
 */
export function staleRefreshError(state: RefreshLike | null | undefined, collectedAt: string | null | undefined) {
  if (!state || state.status !== "error" || state.in_progress) return null;
  if (collectedAt && state.finished_at && new Date(collectedAt) > new Date(state.finished_at)) return null;
  return state.error ?? "неизвестная ошибка";
}
