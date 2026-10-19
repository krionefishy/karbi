export interface RefreshLike {
  status: "queued" | "running" | "success" | "error";
  in_progress: boolean;
  finished_at: string | null;
  error: string | null;
}

export function staleRefreshError(state: RefreshLike | null | undefined, collectedAt: string | null | undefined) {
  if (!state || state.status !== "error" || state.in_progress) return null;
  if (collectedAt && state.finished_at && new Date(collectedAt) > new Date(state.finished_at)) return null;
  return state.error ?? "неизвестная ошибка";
}
