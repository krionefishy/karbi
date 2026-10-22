import {
  ApiError,
  OUTCOME_CAPTIONS,
  getSubmission,
  isFinal,
  listSubmissions,
  me,
  moscowToday,
  statusText,
  summary,
  type ClientConfig,
  type Outcome,
  type SubmissionOut,
} from "./api";
import { captureActiveTab, fileNameFor, fitForUpload, isAllowedUrl, type Capture, type TabsApi } from "./capture";
import { sendToWorker } from "./messages";
import { idbStore, newItem } from "./queue";
import {
  downloadUrl,
  extensionVersion,
  loadSettings,
  pauseFor,
  pauseText,
  saveSettings,
  upsertRecent,
  type RecentEntry,
  type Settings,
} from "./settings";

// Снимок — первым делом, до всего остального: страница под попапом не должна успеть измениться.
const tabs: TabsApi = {
  query: (info) => chrome.tabs.query(info),
  captureVisibleTab: (windowId, options) => chrome.tabs.captureVisibleTab(windowId, options),
};
const captured: Promise<Capture | Error> = captureActiveTab(tabs, () => new Date(), window.devicePixelRatio).catch((error: unknown) =>
  error instanceof Error ? error : new Error(String(error)),
);

const POLL_INTERVAL_MS = 3_000;
const POLL_LIMIT_MS = 60_000;
const store = idbStore();
const timeFormat = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit" });

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const sections = ["loading", "not-paired", "not-chat", "capture-error", "capture"] as const;

function show(section: (typeof sections)[number]): void {
  for (const id of sections) $(id).hidden = id !== section;
}

function setStatus(text: string, tone: "ok" | "warn" | "bad" | "" = ""): void {
  const status = $("status");
  status.className = tone;
  status.textContent = text;
}

function clientConfig(settings: Settings): ClientConfig {
  return { backendUrl: settings.backendUrl, token: settings.token, version: extensionVersion() };
}

function renderBanner(settings: Settings): void {
  const banner = $("banner");
  const text = settings.token || settings.pause ? pauseText(settings.pause, Boolean(settings.token)) : null;
  banner.hidden = !text;
  banner.replaceChildren();
  if (!text) return;
  banner.append(text);
  if (settings.pause?.kind === "upgrade" && settings.backendUrl) {
    const link = document.createElement("a");
    link.href = downloadUrl(settings.backendUrl);
    link.target = "_blank";
    link.textContent = " Скачать новую версию";
    banner.append(link);
  }
}

/** Ошибка API в попапе: 401/403/426 ставят очередь на паузу и обновляют бейдж. */
async function handleApiError(error: unknown): Promise<void> {
  if (error instanceof ApiError && (await pauseFor(error))) {
    renderBanner(await loadSettings());
    await sendToWorker({ type: "badge" }).catch(() => undefined);
  }
}

function sourceLine(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.host}${parsed.pathname}${parsed.search}`;
  } catch {
    return url;
  }
}

let capture: Capture | null = null;
let decided = false;

function decide(settings: Settings, result: Capture | Error): void {
  if (!settings.token && !settings.pause) {
    show("not-paired");
    return;
  }
  if (result instanceof Error) {
    $("capture-error").textContent = result.message;
    show("capture-error");
    return;
  }
  if (!isAllowedUrl(result.url, settings.allowedUrlPrefixes)) {
    show("not-chat");
    return;
  }
  if (decided) return;
  decided = true;
  capture = result;
  $<HTMLImageElement>("preview").src = result.dataUrl;
  $("source").textContent = sourceLine(result.url);
  $("source").title = result.url;
  show("capture");
}

async function poll(serverId: string, settings: Settings, clientId: string): Promise<void> {
  const started = Date.now();
  while (Date.now() - started < POLL_LIMIT_MS) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    let item: SubmissionOut;
    try {
      item = await getSubmission(clientConfig(settings), serverId);
    } catch (error) {
      await handleApiError(error);
      if (error instanceof ApiError && error.kind !== "retry") return;
      continue;
    }
    await upsertRecent(clientId, { status: item.status, reasonText: item.reason_text });
    showVerdict(item);
    if (isFinal(item.status)) {
      void renderFooter();
      return;
    }
  }
  setStatus("В обработке: итог появится в списке отправок", "");
}

function showVerdict(item: SubmissionOut): void {
  const tone = item.status === "accepted" ? "ok" : item.status === "rejected" ? "bad" : item.status === "processing" ? "" : "warn";
  let text = statusText(item);
  if (item.status === "duplicate" && item.duplicate_received_at) {
    text += ` (впервые ${new Date(item.duplicate_received_at).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" })})`;
  }
  setStatus(text, tone);
}

async function sendOutcome(outcome: Outcome, button: HTMLButtonElement): Promise<void> {
  if (!capture) return;
  const current = capture;
  // Один захват — одна запись: после клика кнопки этого снимка больше не работают.
  capture = null;
  for (const other of document.querySelectorAll<HTMLButtonElement>("[data-outcome]")) other.disabled = true;
  button.classList.add("chosen");
  setStatus("Отправляем…");
  try {
    const blob = await fitForUpload(current.blob);
    const item = newItem(
      {
        id: current.id,
        blob,
        filename: fileNameFor(blob),
        outcome,
        url: current.url,
        capturedAt: current.capturedAt,
        tzOffsetMin: current.tzOffsetMin,
        viewportW: current.viewportW,
        viewportH: current.viewportH,
        dpr: current.dpr,
      },
      Date.now(),
    );
    await store.put(item);
    await upsertRecent(item.id, { outcome, capturedAt: item.capturedAt, state: "queued" });
  } catch (error) {
    setStatus(`Не удалось сохранить снимок: ${error instanceof Error ? error.message : String(error)}`, "bad");
    return;
  }

  const reply = await sendToWorker({ type: "flush" }).catch((error: unknown) => ({ ok: false as const, error: String(error) }));
  const result = reply.ok ? reply.results.find((entry) => entry.id === current.id) : undefined;
  const settings = await loadSettings();
  renderBanner(settings);
  if (!result) {
    setStatus("Скриншот в очереди: отправим, как только сервис будет доступен", "warn");
  } else if (result.state === "sent" && result.submission) {
    showVerdict(result.submission);
    if (!isFinal(result.submission.status)) void poll(result.submission.id, settings, current.id);
  } else if (result.state === "dropped") {
    setStatus(`Не принят: ${result.error ?? "ошибка"}`, "bad");
  } else if (result.state === "failed") {
    setStatus(`Не отправлено: ${result.error ?? "ошибка"}`, "bad");
  } else if (result.state === "paused") {
    setStatus("Скриншот сохранён в очереди и уйдёт после переподключения", "warn");
  } else {
    setStatus(`${result.error ?? "Нет связи"}. Скриншот в очереди, отправим сами`, "warn");
  }
  void renderFooter();
}

function recentRow(time: string, outcome: Outcome | null, state: string, tone: string): HTMLLIElement {
  const row = document.createElement("li");
  const timeCell = document.createElement("span");
  timeCell.className = "time";
  timeCell.textContent = time;
  const outcomeCell = document.createElement("span");
  outcomeCell.className = "outcome";
  outcomeCell.textContent = outcome ? OUTCOME_CAPTIONS[outcome] : "—";
  const stateCell = document.createElement("span");
  stateCell.className = `state ${tone}`;
  stateCell.textContent = state;
  stateCell.title = state;
  row.append(timeCell, outcomeCell, stateCell);
  return row;
}

function localState(entry: RecentEntry): [string, string] {
  if (entry.state === "dropped") return [`Не принят: ${entry.error ?? ""}`, "bad"];
  if (entry.state === "failed") return ["Не отправлено", "bad"];
  if (entry.state === "queued") return ["В очереди", "warn"];
  return [entry.status ? statusText({ status: entry.status, reason_text: entry.reasonText }) : "Отправлен", ""];
}

async function renderFooter(): Promise<void> {
  const settings = await loadSettings();
  const items = await store.all();
  const pending = items.filter((item) => item.state === "pending").length;
  const failed = items.length - pending;
  $("queue-line").textContent = [pending ? `В очереди: ${pending}` : "", failed ? `не отправлено: ${failed}` : ""].filter(Boolean).join(" · ");

  const local = settings.recent.filter((entry) => entry.state !== "sent");
  let rows: { at: number; row: HTMLLIElement }[] = local.map((entry) => {
    const [state, tone] = localState(entry);
    const at = Date.parse(entry.capturedAt);
    return { at, row: recentRow(timeFormat.format(at), entry.outcome, state, tone) };
  });
  if (settings.token) {
    const config = clientConfig(settings);
    try {
      const [today, server] = await Promise.all([summary(config, moscowToday()), listSubmissions(config, 5)]);
      const counts = today.counts;
      $("summary").textContent =
        `Сегодня: полож ${counts.positive} · доп ${counts.amended} · удален ${counts.deleted} · ревью ${today.pending_review}` +
        (today.preliminary ? " (пробно)" : "");
      rows = rows.concat(
        server.map((item) => {
          const at = Date.parse(item.received_at);
          const tone = item.status === "accepted" ? "ok" : item.status === "rejected" ? "bad" : item.status === "processing" ? "" : "warn";
          return { at, row: recentRow(timeFormat.format(at), item.outcome, statusText(item), tone) };
        }),
      );
    } catch (error) {
      await handleApiError(error);
      $("summary").textContent = "Сегодня: нет связи с сервисом";
      rows = rows.concat(
        settings.recent
          .filter((entry) => entry.state === "sent")
          .map((entry) => {
            const [state, tone] = localState(entry);
            const at = Date.parse(entry.capturedAt);
            return { at, row: recentRow(timeFormat.format(at), entry.outcome, state, tone) };
          }),
      );
    }
  }
  rows.sort((a, b) => b.at - a.at);
  $("recent").replaceChildren(...rows.slice(0, 5).map((entry) => entry.row));
}

async function refreshMe(settings: Settings): Promise<Settings> {
  if (!settings.token) return settings;
  try {
    const profile = await me(clientConfig(settings));
    const patch = { username: profile.username, minVersion: profile.min_version, allowedUrlPrefixes: profile.allowed_url_prefixes };
    // /me ответил — блокировку сняли или версию обновили: очередь можно продолжать.
    const resumed = Boolean(settings.pause);
    await saveSettings({ ...patch, pause: null });
    if (resumed) void sendToWorker({ type: "flush" }).catch(() => undefined);
    return { ...settings, ...patch, pause: null };
  } catch (error) {
    await handleApiError(error);
    return loadSettings();
  }
}

async function main(): Promise<void> {
  $("version").textContent = `v${extensionVersion()}`;
  for (const link of document.querySelectorAll("[data-open-options]")) {
    link.addEventListener("click", () => void chrome.runtime.openOptionsPage());
  }
  for (const button of document.querySelectorAll<HTMLButtonElement>("[data-outcome]")) {
    button.addEventListener("click", () => void sendOutcome(button.dataset.outcome as Outcome, button));
  }
  const settings = await loadSettings();
  renderBanner(settings);
  const result = await captured;
  decide(settings, result);
  void renderFooter();
  // Список разрешённых адресов мог поменяться на сервере — перепроверяем, пока менеджер не нажал исход.
  const fresh = await refreshMe(settings);
  renderBanner(fresh);
  if (!decided && fresh.token) decide(fresh, result);
}

void main();
