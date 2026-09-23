/** Сервис-воркер: по будильнику и по просьбе бота берёт код получения со страницы WB и отдаёт бэкенду. */

import { collectFromPage, type PageResult } from "./page";
import { browserName, loadSettings, saveSettings, type Message, type Settings } from "./shared";

const DELIVERIES_URL = "https://www.wildberries.ru/lk/deliveries";
const HEARTBEAT_ALARM = "heartbeat";
const COLLECT_ALARM = "collect";
const HEARTBEAT_MINUTES = 10;
// Код меняется в полночь по Москве; второй заход днём — на случай, если ночью браузер спал.
const COLLECT_HOURS_MSK = [0, 12];
const COLLECT_MINUTE = 5;
const PAGE_TIMEOUT_MS = 45_000;

let running: Promise<void> | null = null;

chrome.runtime.onInstalled.addListener(() => {
  void scheduleAll();
});
chrome.runtime.onStartup.addListener(() => {
  void scheduleAll();
});
chrome.action.onClicked.addListener(() => {
  void chrome.runtime.openOptionsPage();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === COLLECT_ALARM) {
    void collect("schedule").finally(scheduleCollect);
  } else if (alarm.name === HEARTBEAT_ALARM) {
    void heartbeat();
  }
});

chrome.runtime.onMessage.addListener((message: Message, _sender, respond) => {
  void (async () => {
    try {
      if (message.type === "status") {
        respond({ ok: true, settings: await loadSettings() });
      } else if (message.type === "pair") {
        respond({ ok: true, settings: await pair(message.backendUrl, message.code) });
      } else if (message.type === "run") {
        await collect("manual");
        respond({ ok: true, settings: await loadSettings() });
      } else if (message.type === "unpair") {
        await saveSettings({ token: "", sellerName: "", lastState: null, lastError: null, hasCodeToday: false });
        respond({ ok: true, settings: await loadSettings() });
      }
    } catch (error) {
      respond({ ok: false, error: error instanceof Error ? error.message : String(error) });
    }
  })();
  return true;
});

async function scheduleAll(): Promise<void> {
  await chrome.alarms.create(HEARTBEAT_ALARM, { periodInMinutes: HEARTBEAT_MINUTES });
  await scheduleCollect();
}

/** Следующий из заходов по московскому времени, сегодня или завтра. */
function nextCollectAt(now = new Date()): Date {
  const candidates = COLLECT_HOURS_MSK.map((hour) => {
    const moscow = new Date(now.toLocaleString("en-US", { timeZone: "Europe/Moscow" }));
    const offset = now.getTime() - moscow.getTime();
    const target = new Date(moscow);
    target.setHours(hour, COLLECT_MINUTE, 0, 0);
    if (target <= moscow) target.setDate(target.getDate() + 1);
    return new Date(target.getTime() + offset);
  });
  return candidates.reduce((soonest, item) => (item < soonest ? item : soonest));
}

async function scheduleCollect(): Promise<void> {
  const when = nextCollectAt();
  await chrome.alarms.create(COLLECT_ALARM, { when: when.getTime() });
  await saveSettings({ nextRunAt: when.toISOString() });
}

async function api<T>(settings: Settings, path: string, body: unknown): Promise<T> {
  const response = await fetch(`${settings.backendUrl.replace(/\/$/, "")}/api/v1/wb/returns/extension${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${settings.token}` },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `${response.status}`;
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail : detail;
    } catch {
      /* тело не JSON — оставляем статус */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

async function pair(backendUrl: string, code: string): Promise<Settings> {
  const base = backendUrl.trim().replace(/\/$/, "");
  if (!/^https?:\/\//.test(base)) throw new Error("Укажите адрес вида https://…");
  const settings = await loadSettings();
  const response = await fetch(`${base}/api/v1/wb/returns/extension/pair`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code: code.trim(), install_id: settings.installId, browser: browserName() }),
  });
  if (!response.ok) {
    let detail = "Код не подошёл";
    try {
      const payload = await response.json();
      detail = typeof payload.detail === "string" ? payload.detail : detail;
    } catch {
      /* без тела */
    }
    throw new Error(detail);
  }
  const payload = (await response.json()) as { token: string; seller_name: string };
  await saveSettings({ backendUrl: base, token: payload.token, sellerName: payload.seller_name, lastError: null });
  await scheduleAll();
  void collect("pair");
  return loadSettings();
}

async function heartbeat(): Promise<void> {
  const settings = await loadSettings();
  if (!settings.token) return;
  try {
    const reply = await api<{ has_code_today: boolean; refresh_code: boolean; seller_name: string }>(
      settings,
      "/heartbeat",
      { state: settings.lastState ?? "ok", error: settings.lastError },
    );
    await saveSettings({ hasCodeToday: reply.has_code_today, sellerName: reply.seller_name || settings.sellerName });
    if (reply.refresh_code) await collect("task");
  } catch (error) {
    await saveSettings({ lastError: `heartbeat: ${error instanceof Error ? error.message : String(error)}` });
  }
}

/** Один заход: открыть страницу доставок, снять коды и список, отправить, закрыть. Заходы не накладываются. */
async function collect(reason: string): Promise<void> {
  if (running) return running;
  running = (async () => {
    const settings = await loadSettings();
    if (!settings.token) return;
    let state: Settings["lastState"] = "ok";
    let error: string | null = null;
    try {
      const page = await readPage();
      if (!page.loggedIn) {
        state = "needs_login";
        error = page.error ?? `deliveries ${page.deliveriesStatus}, codes ${page.codesStatus}`;
      } else {
        const codes = normalizeCodes(page.codes);
        if (codes.length) {
          await api(settings, "/codes", { codes });
          await saveSettings({ lastCodeAt: new Date().toISOString() });
        } else {
          state = "error";
          error = `codes ${page.codesStatus}: ${JSON.stringify(page.codes).slice(0, 300)}`;
        }
        const items = normalizeDeliveries(page.deliveries);
        if (items.length) await api(settings, "/deliveries", { items });
      }
    } catch (caught) {
      state = "error";
      error = `${reason}: ${caught instanceof Error ? caught.message : String(caught)}`;
    }
    await saveSettings({ lastRunAt: new Date().toISOString(), lastState: state, lastError: error });
    try {
      const reply = await api<{ has_code_today: boolean }>(settings, "/heartbeat", { state, error });
      await saveSettings({ hasCodeToday: reply.has_code_today });
    } catch {
      /* heartbeat повторится по будильнику */
    }
  })().finally(() => {
    running = null;
  });
  return running;
}

async function readPage(): Promise<PageResult> {
  const existing = (await chrome.tabs.query({ url: `${DELIVERIES_URL}*` }))[0];
  const tab = existing ?? (await chrome.tabs.create({ url: DELIVERIES_URL, active: false }));
  if (tab.id === undefined) throw new Error("не удалось открыть вкладку");
  const tabId = tab.id;
  try {
    if (!existing || tab.status !== "complete") await waitForLoad(tabId);
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: collectFromPage,
    });
    const result = injection?.result as PageResult | undefined;
    if (!result) throw new Error("страница не ответила");
    return result;
  } finally {
    if (!existing) await chrome.tabs.remove(tabId).catch(() => undefined);
  }
}

function waitForLoad(tabId: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      reject(new Error("страница WB не загрузилась за 45 с"));
    }, PAGE_TIMEOUT_MS);
    function listener(updatedId: number, info: chrome.tabs.TabChangeInfo) {
      if (updatedId === tabId && info.status === "complete") {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        // SPA дорисовывает профиль после load: даём ей секунды на токен и cookie.
        setTimeout(resolve, 4000);
      }
    }
    chrome.tabs.onUpdated.addListener(listener);
  });
}

interface CodeRow {
  date: string;
  code: string;
  ext_code: string;
  qr: string;
  ext_qr: string;
}

/** Ответ `otp/get`: массив по дням с полями date, code, extCode, qr, extQr — в любой обёртке. */
function normalizeCodes(payload: unknown): CodeRow[] {
  const list = findArray(payload, (item) => typeof item === "object" && item !== null && "date" in item);
  const rows: CodeRow[] = [];
  for (const item of list) {
    const record = item as Record<string, unknown>;
    const date = String(record.date ?? "").slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) continue;
    rows.push({
      date,
      code: String(record.code ?? ""),
      ext_code: String(record.extCode ?? record.ext_code ?? ""),
      qr: String(record.qr ?? ""),
      ext_qr: String(record.extQr ?? record.ext_qr ?? ""),
    });
  }
  return rows;
}

function normalizeDeliveries(payload: unknown): unknown[] {
  return findArray(payload, (item) => typeof item === "object" && item !== null).slice(0, 200);
}

function findArray(value: unknown, matches: (item: unknown) => boolean, depth = 0): unknown[] {
  if (depth > 5 || value == null) return [];
  if (Array.isArray(value)) return value.some(matches) ? value : [];
  if (typeof value === "object") {
    for (const inner of Object.values(value as Record<string, unknown>)) {
      const found = findArray(inner, matches, depth + 1);
      if (found.length) return found;
    }
  }
  return [];
}
