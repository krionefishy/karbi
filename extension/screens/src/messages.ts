import type { ItemResult } from "./queue";

/** Сообщения страниц расширения service worker'у. Blob так не передать — только id записи в IndexedDB. */
export type Message = { type: "flush" } | { type: "badge" };

export type FlushReply = { ok: true; results: ItemResult[] } | { ok: false; error: string };

export function sendToWorker(message: { type: "flush" }): Promise<FlushReply>;
export function sendToWorker(message: { type: "badge" }): Promise<{ ok: boolean }>;
export function sendToWorker(message: Message): Promise<unknown> {
  return chrome.runtime.sendMessage(message);
}
