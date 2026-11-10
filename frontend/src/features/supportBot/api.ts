import { apiRequest } from "../../api/http";
import type {
  BotDialog,
  BotMode,
  DialogPage,
  DialogScope,
  JournalFilter,
  MonitorResponse,
  EscalationItem,
  ProductList,
  ReplyDialog,
  ReplySummary,
  SendPage,
  SupportBot,
  Pace,
  PaceInput,
  Template,
} from "./types";

const root = "/api/v1/wb/support-bot";
const base = (sellerId: string) => `${root}/sellers/${sellerId}`;

export const PAGE_SIZE = 50;

export const getSupportBot = (sellerId: string) => apiRequest<SupportBot>(base(sellerId));

export const getProducts = (sellerId: string, scope: DialogScope) =>
  apiRequest<ProductList>(`${base(sellerId)}/products?scope=${scope}`);

export function getProductDialogs(sellerId: string, key: number, scope: DialogScope, page: number) {
  const params = new URLSearchParams({ scope, page: String(page), page_size: String(PAGE_SIZE) });
  return apiRequest<DialogPage>(`${base(sellerId)}/products/${key}/dialogs?${params.toString()}`);
}

export const setProductSwitch = (sellerId: string, key: number, enabled: boolean) =>
  apiRequest<void>(`${base(sellerId)}/products/${key}/switch`, { method: "PUT", body: JSON.stringify({ enabled }) });

export function getSends(sellerId: string, filter: JournalFilter) {
  const params = new URLSearchParams({ page: String(filter.page), page_size: String(PAGE_SIZE) });
  if (filter.status) params.set("status", filter.status);
  return apiRequest<SendPage>(`${base(sellerId)}/sends?${params.toString()}`);
}

export const setMode = (sellerId: string, mode: BotMode) =>
  apiRequest<void>(`${base(sellerId)}/mode`, { method: "PUT", body: JSON.stringify({ mode }) });

export const savePace = (sellerId: string, pace: PaceInput) =>
  apiRequest<{ rescheduled: number }>(`${base(sellerId)}/pace`, { method: "PUT", body: JSON.stringify(pace) });

export const setPaused = (paused: boolean) =>
  apiRequest<void>(`${root}/control`, { method: "PUT", body: JSON.stringify({ paused }) });

export const addTemplate = (sellerId: string, text: string) =>
  apiRequest<Template>(`${base(sellerId)}/templates`, { method: "POST", body: JSON.stringify({ text }) });

export const updateTemplate = (sellerId: string, id: number, text: string) =>
  apiRequest<Template>(`${base(sellerId)}/templates/${id}`, { method: "PUT", body: JSON.stringify({ text }) });

export const setTemplateActive = (sellerId: string, id: number, active: boolean) =>
  apiRequest<Template>(`${base(sellerId)}/templates/${id}/active`, {
    method: "PUT",
    body: JSON.stringify({ active }),
  });

export const deleteTemplate = (sellerId: string, id: number) =>
  apiRequest<void>(`${base(sellerId)}/templates/${id}`, { method: "DELETE" });

export const setDialogFlags = (
  sellerId: string,
  dialog: Pick<BotDialog, "chat_id" | "prompt_at">,
  flags: { excluded?: boolean; closed?: boolean },
) =>
  apiRequest<void>(`${base(sellerId)}/dialogs/flags`, {
    method: "POST",
    body: JSON.stringify({ chat_id: dialog.chat_id, prompt_at: dialog.prompt_at, ...flags }),
  });

/** `on`: true — писать, false — не писать, null — вернуть к решению модели. */
export const setDialogSwitch = (
  sellerId: string,
  dialog: Pick<BotDialog, "chat_id" | "prompt_at">,
  on: boolean | null,
) =>
  apiRequest<void>(`${base(sellerId)}/dialogs/switch`, {
    method: "POST",
    body: JSON.stringify({ chat_id: dialog.chat_id, prompt_at: dialog.prompt_at, on }),
  });

export const getReplySummary = (sellerId: string, days: number) =>
  apiRequest<ReplySummary>(`${base(sellerId)}/replies/summary?days=${days}`);

export const getReplyEscalations = (sellerId: string) =>
  apiRequest<{ items: EscalationItem[] }>(`${base(sellerId)}/replies/escalations`);

export function getReplyDialog(sellerId: string, chatId: string, promptAt: string) {
  const params = new URLSearchParams({ chat_id: chatId, prompt_at: promptAt });
  return apiRequest<ReplyDialog>(`${base(sellerId)}/replies/dialog?${params.toString()}`);
}

export const getMonitor = (sellerId: string) => apiRequest<MonitorResponse>(`${base(sellerId)}/monitor`);
