/**
 * Service worker: отправляет очередь, повторяет по будильнику, рисует бейдж.
 * Засыпает между событиями, поэтому состояние только в IndexedDB и chrome.storage.
 */
import { ApiError, submit } from "./api";
import { idbStore, nextWakeAt, processQueue, splitByOwner, type ItemResult, type QueueItem } from "./queue";
import { extensionVersion, loadSettings, pauseFor, setPause, upsertRecent } from "./settings";
import type { Message } from "./messages";

const FLUSH_ALARM = "flush";
const TICK_ALARM = "tick";
const store = idbStore();

async function send(item: QueueItem) {
  const settings = await loadSettings();
  try {
    const submission = await submit(
      { backendUrl: settings.backendUrl, token: settings.token, version: extensionVersion() },
      {
        file: item.blob,
        filename: item.filename,
        outcome: item.outcome,
        clientSubmissionId: item.id,
        clientUrl: item.url,
        clientCapturedAt: item.capturedAt,
        tzOffsetMin: item.tzOffsetMin,
        viewportW: item.viewportW,
        viewportH: item.viewportH,
        dpr: item.dpr,
      },
    );
    return { ok: true as const, submission };
  } catch (error) {
    if (error instanceof ApiError) return { ok: false as const, error };
    return { ok: false as const, error: new ApiError(String(error), 0, "retry") };
  }
}

async function isPaused(): Promise<boolean> {
  const settings = await loadSettings();
  return Boolean(settings.pause) || !settings.token || !settings.backendUrl;
}

async function currentOwner(): Promise<string | null> {
  return (await loadSettings()).owner || null;
}

async function runFlush(): Promise<ItemResult[]> {
  const results = await processQueue({
    store,
    now: Date.now,
    isPaused,
    owner: currentOwner,
    send,
    pause: async (error) => {
      await pauseFor(error);
    },
    onSent: (item, submission) =>
      upsertRecent(item.id, { state: "sent", serverId: submission.id, status: submission.status, reasonText: submission.reason_text, error: null }),
    onDropped: (item, error) => upsertRecent(item.id, { state: "dropped", error: error.message }),
    onRetry: (item) => upsertRecent(item.id, { state: item.state === "failed" ? "failed" : "queued", error: item.lastError }),
  });
  await schedule();
  await refreshBadge();
  return results;
}

// Один прогон за раз: попап и будильник могут попросить одновременно.
let chain: Promise<unknown> = Promise.resolve();
function flush(): Promise<ItemResult[]> {
  const next = chain.then(runFlush, runFlush);
  chain = next.catch(() => undefined);
  return next;
}

async function schedule(): Promise<void> {
  // На паузе будить незачем: очередь продолжат новое сопряжение или обновление расширения.
  const wake = (await isPaused()) ? null : nextWakeAt(await store.all(), await currentOwner());
  if (wake === null) {
    await chrome.alarms.clear(FLUSH_ALARM);
    return;
  }
  // Chrome не будит чаще раза в 30 с.
  await chrome.alarms.create(FLUSH_ALARM, { when: Math.max(wake, Date.now() + 30_000) });
}

async function refreshBadge(): Promise<void> {
  const [settings, all] = await Promise.all([loadSettings(), store.all()]);
  // Чужие записи на бейдж не влияют: их видно только в настройках.
  const items = splitByOwner(all, settings.owner || null).own;
  if (settings.pause || !settings.token) {
    await chrome.action.setBadgeBackgroundColor({ color: "#cf3434" });
    await chrome.action.setBadgeText({ text: "!" });
    return;
  }
  const failed = items.some((item) => item.state === "failed");
  await chrome.action.setBadgeBackgroundColor({ color: failed ? "#9a5b00" : "#2a5ce6" });
  await chrome.action.setBadgeText({ text: items.length ? String(Math.min(items.length, 99)) : "" });
}

chrome.runtime.onMessage.addListener((message: Message, _sender, sendResponse) => {
  if (message?.type === "flush") {
    flush().then(
      (results) => sendResponse({ ok: true, results }),
      (error) => sendResponse({ ok: false, error: String(error) }),
    );
    return true;
  }
  if (message?.type === "badge") {
    refreshBadge().then(
      () => sendResponse({ ok: true }),
      (error) => sendResponse({ ok: false, error: String(error) }),
    );
    return true;
  }
  return false;
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === FLUSH_ALARM || alarm.name === TICK_ALARM) void flush();
});

chrome.runtime.onInstalled.addListener(async (details) => {
  // Новая версия снимает паузу «обновите расширение»; если и она мала, сервер снова ответит 426.
  if (details.reason === "update") {
    const settings = await loadSettings();
    if (settings.pause?.kind === "upgrade") await setPause(null);
  }
  await chrome.alarms.create(TICK_ALARM, { periodInMinutes: 60 });
  void flush();
});

chrome.runtime.onStartup.addListener(() => {
  void flush();
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && ("pause" in changes || "token" in changes || "owner" in changes)) void refreshBadge();
});
