/** Общие типы и хранилище расширения. Всё лежит в chrome.storage.local, кроме кода дня: он у бота. */

export interface Settings {
  backendUrl: string;
  token: string;
  sellerName: string;
  installId: string;
  lastRunAt: string | null;
  lastState: "ok" | "needs_login" | "error" | null;
  lastError: string | null;
  lastCodeAt: string | null;
  hasCodeToday: boolean;
  nextRunAt: string | null;
}

export const DEFAULTS: Settings = {
  backendUrl: "",
  token: "",
  sellerName: "",
  installId: "",
  lastRunAt: null,
  lastState: null,
  lastError: null,
  lastCodeAt: null,
  hasCodeToday: false,
  nextRunAt: null,
};

export async function loadSettings(): Promise<Settings> {
  const stored = (await chrome.storage.local.get(DEFAULTS)) as Partial<Settings>;
  const settings = { ...DEFAULTS, ...stored };
  if (!settings.installId) {
    settings.installId = `${browserName().toLowerCase()}-${crypto.randomUUID().replace(/-/g, "").slice(0, 24)}`;
    await chrome.storage.local.set({ installId: settings.installId });
  }
  return settings;
}

export async function saveSettings(patch: Partial<Settings>): Promise<void> {
  await chrome.storage.local.set(patch);
}

export function browserName(): string {
  const agent = navigator.userAgent;
  const yandex = agent.match(/YaBrowser\/([\d.]+)/);
  if (yandex) return `Яндекс Браузер ${yandex[1].split(".").slice(0, 2).join(".")}`;
  const chrome = agent.match(/Chrome\/([\d.]+)/);
  if (chrome) return `Chrome ${chrome[1].split(".")[0]}`;
  return "Браузер";
}

export type Message =
  | { type: "status" }
  | { type: "run" }
  | { type: "unpair" }
  | { type: "pair"; backendUrl: string; code: string };
