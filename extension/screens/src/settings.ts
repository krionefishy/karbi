import { browserName, normalizeBackendUrl } from "../../src/shared";
import { DEFAULT_ALLOWED_PREFIXES } from "./capture";
import type { ApiError, Outcome, Status } from "./api";

/** Почему очередь стоит: без токена, отозван, сотрудник заблокирован, версия ниже минимальной. */
export type PauseKind = "auth" | "blocked" | "upgrade";

export interface Pause {
  kind: PauseKind;
  message: string;
  at: string;
}

/** Последние отправки этого браузера: что показать в попапе, пока сервер не ответил. */
export interface RecentEntry {
  id: string;
  outcome: Outcome;
  capturedAt: string;
  state: "queued" | "sent" | "failed" | "dropped";
  serverId: string | null;
  status: Status | null;
  reasonText: string | null;
  error: string | null;
}

export interface Settings {
  backendUrl: string;
  token: string;
  username: string;
  installId: string;
  allowedUrlPrefixes: string[];
  minVersion: string;
  pause: Pause | null;
  recent: RecentEntry[];
}

export const DEFAULT_BACKEND_URL: string = import.meta.env.VITE_BACKEND_URL ?? "";
export const DOWNLOAD_PATH = "/extension/marketplace-auto-screenshots.zip";
const RECENT_LIMIT = 10;

export const DEFAULTS: Settings = {
  backendUrl: DEFAULT_BACKEND_URL,
  token: "",
  username: "",
  installId: "",
  allowedUrlPrefixes: [...DEFAULT_ALLOWED_PREFIXES],
  minVersion: "",
  pause: null,
  recent: [],
};

export function extensionVersion(): string {
  return chrome.runtime.getManifest().version;
}

export async function loadSettings(): Promise<Settings> {
  const stored = (await chrome.storage.local.get(DEFAULTS)) as Partial<Settings>;
  const settings: Settings = { ...DEFAULTS, ...stored };
  settings.backendUrl ||= DEFAULT_BACKEND_URL;
  if (!settings.allowedUrlPrefixes?.length) settings.allowedUrlPrefixes = [...DEFAULT_ALLOWED_PREFIXES];
  if (!settings.installId) {
    // Токен привязан к установке: новая пара того же install_id отзывает прежний токен на сервере.
    settings.installId = `${browserName().toLowerCase().replace(/\s+/g, "-")}-${crypto.randomUUID().replace(/-/g, "").slice(0, 24)}`;
    await chrome.storage.local.set({ installId: settings.installId });
  }
  return settings;
}

export async function saveSettings(patch: Partial<Settings>): Promise<void> {
  await chrome.storage.local.set(patch);
}

export async function setPause(pause: Pause | null): Promise<void> {
  await saveSettings({ pause });
}

/** 401 — токен больше не годится: стираем его, нужно новое сопряжение. 403 и 426 — пауза с токеном. */
export async function pauseFor(error: ApiError): Promise<boolean> {
  if (error.kind !== "auth" && error.kind !== "blocked" && error.kind !== "upgrade") return false;
  const pause: Pause = { kind: error.kind, message: error.message, at: new Date().toISOString() };
  await saveSettings(error.kind === "auth" ? { pause, token: "" } : { pause });
  return true;
}

export function pauseText(pause: Pause | null, paired: boolean): string | null {
  if (pause?.kind === "upgrade") return "Обновите расширение: эта версия больше не принимается сервисом.";
  if (pause?.kind === "blocked") return pause.message || "Доступ сотрудника заблокирован.";
  if (pause?.kind === "auth") return "Подключите расширение заново: откройте настройки и введите код со страницы.";
  if (!paired) return "Не подключено: введите код со страницы «Контроль скриншотов» → «Расширение».";
  return null;
}

/** Общий normalizeBackendUrl из пакета возвратов, но со своим текстом для пустого поля. */
export function backendOrigin(raw: string): string {
  if (!raw.trim()) throw new Error("Укажите адрес Marketplace Auto — тот, на котором открыта страница «Контроль скриншотов»");
  return normalizeBackendUrl(raw);
}

export function downloadUrl(backendUrl: string): string {
  return `${backendUrl}${DOWNLOAD_PATH}`;
}

/** Новая запись создаётся, только если в patch есть исход и время захвата; иначе обновляется существующая. */
export async function upsertRecent(id: string, patch: Partial<RecentEntry>): Promise<void> {
  const { recent } = (await chrome.storage.local.get({ recent: [] })) as { recent: RecentEntry[] };
  const index = recent.findIndex((entry) => entry.id === id);
  if (index >= 0) {
    recent[index] = { ...recent[index], ...patch };
  } else if (patch.outcome && patch.capturedAt) {
    recent.unshift({
      id,
      outcome: patch.outcome,
      capturedAt: patch.capturedAt,
      state: patch.state ?? "queued",
      serverId: patch.serverId ?? null,
      status: patch.status ?? null,
      reasonText: patch.reasonText ?? null,
      error: patch.error ?? null,
    });
  } else {
    return;
  }
  await chrome.storage.local.set({ recent: recent.slice(0, RECENT_LIMIT) });
}
