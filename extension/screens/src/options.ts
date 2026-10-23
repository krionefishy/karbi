import { browserName } from "../../src/shared";
import { ApiError, OUTCOME_CAPTIONS, normalizePairingCode, pair } from "./api";
import { sendToWorker } from "./messages";
import { foreignQueueText, idbStore, removeForeign, resetForRetry, splitByOwner, type QueueItem } from "./queue";
import {
  backendOrigin,
  downloadUrl,
  extensionVersion,
  loadSettings,
  pauseText,
  savePairing,
  saveSettings,
  type Settings,
} from "./settings";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const store = idbStore();
const stamp = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });

function message(text: string, tone: "ok" | "bad" | "" = ""): void {
  $("message").className = tone;
  $("message").textContent = text;
}

function renderSettings(settings: Settings): void {
  const paired = Boolean(settings.token);
  const backend = $<HTMLInputElement>("backend");
  if (document.activeElement !== backend) backend.value = settings.backendUrl;
  const state = $("state");
  const problem = pauseText(settings.pause, paired);
  state.className = problem ? "bad" : "ok";
  state.textContent = problem ?? "подключено";
  $("username").textContent = settings.username || "—";
  $("service").textContent = paired ? settings.backendUrl : "—";
  $("version").textContent = extensionVersion() + (settings.minVersion ? ` (сервис принимает от ${settings.minVersion})` : "");
  const upgrade = settings.pause?.kind === "upgrade" && Boolean(settings.backendUrl);
  $("download").hidden = !upgrade;
  if (upgrade) $<HTMLAnchorElement>("download-link").href = downloadUrl(settings.backendUrl);
  $<HTMLButtonElement>("unpair").disabled = !paired;
}

function queueState(item: QueueItem): [string, string] {
  if (item.state === "failed") return [`не отправлено${item.lastError ? `: ${item.lastError}` : ""}`, "bad"];
  if (item.attempts === 0) return ["ждёт отправки", ""];
  return [`повтор ${stamp.format(item.nextAttemptAt)} (попыток: ${item.attempts}${item.lastError ? `, ${item.lastError}` : ""})`, "warn"];
}

function actionButton(label: string, context: string, onClick: () => Promise<void>): HTMLButtonElement {
  const button = document.createElement("button");
  button.textContent = label;
  button.setAttribute("aria-label", `${label}: ${context}`);
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await onClick();
    } finally {
      await refresh();
    }
  });
  return button;
}

async function renderQueue(settings: Settings): Promise<void> {
  // Чужие записи (другой сотрудник в этом браузере) не показываем построчно и не отправляем.
  const { own: items, foreign } = splitByOwner(await store.all(), settings.owner || null);
  $("foreign").hidden = foreign.length === 0;
  $("foreign-text").textContent = foreign.length ? foreignQueueText(foreign.length) : "";
  $("queue-empty").hidden = items.length > 0;
  $("queue").hidden = items.length === 0;
  const rows = items.map((item) => {
    const row = document.createElement("tr");
    const [text, tone] = queueState(item);
    const cells = [stamp.format(new Date(item.capturedAt)), OUTCOME_CAPTIONS[item.outcome], text].map((value, index) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      if (index === 2) cell.className = tone;
      return cell;
    });
    const actions = document.createElement("td");
    const context = `снимок ${stamp.format(new Date(item.capturedAt))}`;
    actions.append(
      actionButton("Повторить", context, async () => {
        const current = await store.get(item.id);
        if (!current) return;
        await store.put(resetForRetry(current, Date.now()));
        await sendToWorker({ type: "flush" });
      }),
      actionButton("Удалить", context, async () => {
        if (!confirm("Удалить скриншот из очереди? Он не будет отправлен.")) return;
        await store.remove(item.id);
        await sendToWorker({ type: "badge" });
      }),
    );
    row.append(...cells, actions);
    return row;
  });
  $("queue").querySelector("tbody")!.replaceChildren(...rows);
}

async function refresh(): Promise<void> {
  const settings = await loadSettings();
  renderSettings(settings);
  await renderQueue(settings);
}

$("pair").addEventListener("click", async () => {
  const button = $<HTMLButtonElement>("pair");
  button.disabled = true;
  message("Подключаем…");
  try {
    const backendUrl = backendOrigin($<HTMLInputElement>("backend").value);
    $<HTMLInputElement>("backend").value = backendUrl;
    const code = normalizePairingCode($<HTMLInputElement>("code").value);
    if (!code) throw new Error("Код — 8 букв и цифр со страницы «Контроль скриншотов» → «Расширение»");
    const granted = await chrome.permissions.request({ origins: [`${backendUrl}/*`] });
    if (!granted) throw new Error("Без доступа к адресу сервиса расширение не сможет отправлять скриншоты");
    const settings = await loadSettings();
    const reply = await pair(
      { backendUrl, version: extensionVersion() },
      { code, install_id: settings.installId, browser: browserName(), ext_version: extensionVersion() },
    );
    const { owner } = await savePairing(backendUrl, reply);
    const foreign = splitByOwner(await store.all(), owner).foreign.length;
    $<HTMLInputElement>("code").value = "";
    const who = `Подключено${reply.username ? `: ${reply.username}` : ""}.`;
    message(
      foreign
        ? `${who} ${foreignQueueText(foreign)}: от вашего имени они не уйдут.`
        : `${who} Очередь отправки продолжена.`,
      "ok",
    );
    await sendToWorker({ type: "flush" }).catch(() => undefined);
  } catch (error) {
    if (error instanceof ApiError && error.kind === "rate") {
      const minutes = error.retryAfterSec ? Math.max(1, Math.ceil(error.retryAfterSec / 60)) : null;
      message(`Слишком много попыток ввести код. Повторите${minutes ? ` через ${minutes} мин` : " позже"}.`, "bad");
    } else {
      message(error instanceof Error ? error.message : String(error), "bad");
    }
  } finally {
    button.disabled = false;
    await refresh();
  }
});

$("unpair").addEventListener("click", async () => {
  // Очередь не трогаем и владельца не стираем: записи уйдут, только когда подключится тот же сотрудник.
  await saveSettings({ token: "", username: "", pause: null });
  message("Отключено. Скриншоты в очереди остаются за вами до нового подключения или удаления.");
  await sendToWorker({ type: "badge" }).catch(() => undefined);
  await refresh();
});

$("foreign-delete").addEventListener("click", async () => {
  const button = $<HTMLButtonElement>("foreign-delete");
  if (!confirm("Удалить скриншоты другого сотрудника из очереди? Они не будут отправлены.")) return;
  button.disabled = true;
  try {
    const settings = await loadSettings();
    const removed = await removeForeign(store, settings.owner || null);
    message(removed ? `Удалено из очереди: ${removed}` : "", "");
    await sendToWorker({ type: "badge" }).catch(() => undefined);
  } finally {
    button.disabled = false;
    await refresh();
  }
});

void refresh();
setInterval(() => void refresh(), 10_000);
