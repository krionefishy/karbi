import { apiRequest } from "../../api/http";
import type {
  BotDialog,
  BotMode,
  DialogFilter,
  DialogPage,
  JournalFilter,
  EscalationItem,
  ManualVerdict,
  ReplyDialog,
  ReplySummary,
  SendPage,
  SupportBot,
  Template,
} from "./types";

const root = "/api/v1/wb/support-bot";
const base = (sellerId: string) => `${root}/sellers/${sellerId}`;

export const PAGE_SIZE = 50;

export const getSupportBot = (sellerId: string) => apiRequest<SupportBot>(base(sellerId));

export function getDialogs(sellerId: string, filter: DialogFilter) {
  const params = new URLSearchParams({ page: String(filter.page), page_size: String(PAGE_SIZE) });
  if (filter.state) params.set("state", filter.state);
  if (filter.flag) params.set("flag", filter.flag);
  return apiRequest<DialogPage>(`${base(sellerId)}/dialogs?${params.toString()}`);
}

export function getSends(sellerId: string, filter: JournalFilter) {
  const params = new URLSearchParams({ page: String(filter.page), page_size: String(PAGE_SIZE) });
  if (filter.status) params.set("status", filter.status);
  return apiRequest<SendPage>(`${base(sellerId)}/sends?${params.toString()}`);
}

export const setMode = (sellerId: string, mode: BotMode) =>
  apiRequest<void>(`${base(sellerId)}/mode`, { method: "PUT", body: JSON.stringify({ mode }) });

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

export const setManualVerdict = (
  sellerId: string,
  dialog: Pick<BotDialog, "chat_id" | "prompt_at">,
  value: ManualVerdict | null,
) =>
  apiRequest<void>(`${base(sellerId)}/dialogs/manual-verdict`, {
    method: "POST",
    body: JSON.stringify({ chat_id: dialog.chat_id, prompt_at: dialog.prompt_at, value }),
  });

export const getReplySummary = (sellerId: string, days: number) =>
  apiRequest<ReplySummary>(`${base(sellerId)}/replies/summary?days=${days}`);

export const getReplyEscalations = (sellerId: string) =>
  apiRequest<{ items: EscalationItem[] }>(`${base(sellerId)}/replies/escalations`);

export function getReplyDialog(sellerId: string, chatId: string, promptAt: string) {
  const params = new URLSearchParams({ chat_id: chatId, prompt_at: promptAt });
  return apiRequest<ReplyDialog>(`${base(sellerId)}/replies/dialog?${params.toString()}`);
}
