/** Сервис-воркер: по будильнику и по просьбе бота берёт код получения со страницы WB и отдаёт бэкенду. */

import { collectFromPage, type PageResult } from "./page";
import { browserName, loadSettings, saveSettings, type Message, type Settings } from "./shared";

const DELIVERIES_URL = "https://www.wildberries.ru/lk/deliveries";
const CODES_URL = "https://delivery-code.wildberries.ru/delivery-code/api/v1/otp/get";
const HEARTBEAT_ALARM = "heartbeat";
const COLLECT_ALARM = "collect";
const RETRY_ALARM = "retry";
const RETRY_MINUTES = 10;
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
  } else if (alarm.name === RETRY_ALARM) {
    void collect("retry");
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
      if (!page.jwt) {
        state = "needs_login";
        error = `в localStorage нет токена сессии (доставки ${page.deliveriesStatus}${page.error ? `, ${page.error}` : ""})`;
      } else {
        const codesReply = await fetchCodes(page.jwt, page.antibot);
        const codes = normalizeCodes(codesReply.body);
        if (codesReply.status === 401 || codesReply.status === 403) {
          state = "needs_login";
          error = `код ${codesReply.status} с токеном из ${page.jwtSource}: ${codesReply.text.slice(0, 200)}`;
        } else if (codes.length) {
          await api(settings, "/codes", { codes });
          await saveSettings({ lastCodeAt: new Date().toISOString() });
          const items = normalizeDeliveries(page.deliveries);
          if (items.length) await api(settings, "/deliveries", { items });
        } else {
          state = "error";
          error = `код ${codesReply.status}: ${codesReply.text.slice(0, 300)}`;
        }
      }
    } catch (caught) {
      state = "error";
      error = `${reason}: ${caught instanceof Error ? caught.message : String(caught)}`;
    }
    await saveSettings({ lastRunAt: new Date().toISOString(), lastState: state, lastError: error });
    if (state === "ok") {
      await chrome.alarms.clear(RETRY_ALARM);
    } else {
      // Вкладка появится или вход восстановят — не ждать полуночи, попробовать снова.
      await chrome.alarms.create(RETRY_ALARM, { delayInMinutes: RETRY_MINUTES });
    }
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

interface CodesReply {
  status: number;
  body: unknown;
  text: string;
}

/** Код дня — запросом из сервис-воркера: у него есть право на хост, CORS его не касается.
 *
 * Сначала так, как ходит сайт: JWT и заголовки приложения. Если отказ — ещё раз с
 * антибот-cookie, вдруг сервис её проверяет. Ответ отдаём как есть, разбор ниже.
 */
async function fetchCodes(jwt: string, antibot: string | null): Promise<CodesReply> {
  const base: Record<string, string> = {
    Authorization: `Bearer ${jwt}`,
    "WB-AppType": "site",
    "WB-AppVersion": "700",
  };
  const attempts: Record<string, string>[] = [base];
  if (antibot) attempts.push({ ...base, "x-wbaas-token": antibot });
  let last: CodesReply = { status: 0, body: null, text: "" };
  for (const headers of attempts) {
    let response: Response;
    try {
      response = await fetch(CODES_URL, { method: "GET", credentials: "include", headers });
    } catch (caught) {
      last = { status: 0, body: null, text: `fetch: ${caught instanceof Error ? caught.message : String(caught)}` };
      continue;
    }
    const text = await response.text();
    let body: unknown = null;
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
    last = { status: response.status, body, text };
    if (response.ok) return last;
  }
  return last;
}

/** Любая открытая вкладка www.wildberries.ru: запросы к своему домену оттуда те же, что с /lk/deliveries. */
async function findWbTab(): Promise<chrome.tabs.Tab | undefined> {
  const tabs = await chrome.tabs.query({ url: "https://www.wildberries.ru/*" });
  return tabs.find((tab) => tab.url?.startsWith(DELIVERIES_URL)) ?? tabs.find((tab) => tab.status === "complete") ?? tabs[0];
}

async function readPage(): Promise<PageResult> {
  const existing = await findWbTab();
  let tab = existing;
  if (tab === undefined) {
    try {
      tab = await chrome.tabs.create({ url: DELIVERIES_URL, active: false });
    } catch (error) {
      // Антидетект-браузеры (Dolphin) не дают расширению открывать вкладки — тогда
      // код можно взять только из вкладки, которую открыл сам менеджер.
      throw new Error(
        `браузер не дал открыть вкладку (${error instanceof Error ? error.message : String(error)}). ` +
          "Откройте wildberries.ru в этом браузере и оставьте вкладку — расширение возьмёт код из неё",
      );
    }
  }
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
