import { apiRequest } from "../../api/http";
import type { BotDialog, BotMode, BotSend, DialogFilter, DialogPage, SupportBot, Template } from "./types";

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

export const getSends = (sellerId: string) => apiRequest<BotSend[]>(`${base(sellerId)}/sends`);

export const setMode = (sellerId: string, mode: BotMode) =>
  apiRequest<void>(`${base(sellerId)}/mode`, { method: "PUT", body: JSON.stringify({ mode }) });

export const setPaused = (paused: boolean) =>
  apiRequest<void>(`${root}/control`, { method: "PUT", body: JSON.stringify({ paused }) });

export const addTemplate = (sellerId: string, text: string) =>
  apiRequest<Template>(`${base(sellerId)}/templates`, { method: "POST", body: JSON.stringify({ text }) });

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
