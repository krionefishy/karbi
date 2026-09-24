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

/** Адрес сервиса, под который собран архив (`VITE_BACKEND_URL` из PUBLIC_DOMAIN при сборке образа). */
export const DEFAULT_BACKEND_URL: string = import.meta.env.VITE_BACKEND_URL ?? "";

export const DEFAULTS: Settings = {
  backendUrl: DEFAULT_BACKEND_URL,
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
  settings.backendUrl ||= DEFAULT_BACKEND_URL;
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

/** Адрес из поля настроек: без схемы подставляем https, пустой и кривой — понятная ошибка. */
export function normalizeBackendUrl(raw: string): string {
  const value = raw.trim().replace(/\/+$/, "");
  if (!value) throw new Error("Укажите адрес Marketplace Auto — тот, на котором открыта страница «Возвраты WB»");
  const withScheme = /^https?:\/\//i.test(value) ? value : `https://${value}`;
  try {
    return new URL(withScheme).origin;
  } catch {
    throw new Error(`«${value}» не похоже на адрес сайта. Пример: https://marketplace-auto.ru`);
  }
}
