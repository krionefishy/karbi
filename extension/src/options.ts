import type { Message, Settings } from "./shared";

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const momentFormatter = new Intl.DateTimeFormat("ru-RU", { dateStyle: "short", timeStyle: "short" });

function send(message: Message): Promise<{ ok: boolean; settings?: Settings; error?: string }> {
  return chrome.runtime.sendMessage(message);
}

function stamp(value: string | null): string {
  return value ? momentFormatter.format(new Date(value)) : "—";
}

function render(settings: Settings): void {
  const paired = Boolean(settings.token);
  $<HTMLInputElement>("backend").value = settings.backendUrl;
  $("seller").textContent = paired ? settings.sellerName || "подключено" : "не подключено";
  $("last-run").textContent = stamp(settings.lastRunAt);
  const state = $("last-state");
  state.className = settings.lastState === "ok" ? "ok" : settings.lastState === "needs_login" ? "warn" : "bad";
  state.textContent =
    settings.lastState === "ok"
      ? "код получен"
      : settings.lastState === "needs_login"
        ? "нужен вход на wildberries.ru под телефоном владельца"
        : settings.lastState === "error"
          ? `ошибка: ${settings.lastError ?? ""}`
          : "—";
  $("next-run").textContent = stamp(settings.nextRunAt);
  $("code-today").textContent = settings.hasCodeToday ? "у бота есть" : "ещё нет";
  $<HTMLButtonElement>("run").disabled = !paired;
  $<HTMLButtonElement>("unpair").disabled = !paired;
}

async function refresh(): Promise<void> {
  const reply = await send({ type: "status" });
  if (reply.settings) render(reply.settings);
}

$("pair").addEventListener("click", async () => {
  const message = $("message");
  const backendUrl = $<HTMLInputElement>("backend").value.trim().replace(/\/$/, "");
  const code = $<HTMLInputElement>("code").value.trim();
  message.className = "";
  message.textContent = "Подключаем…";
  try {
    // Доступ к адресу сервиса просим здесь, по клику: сервис-воркер не может.
    const origin = new URL(backendUrl).origin;
    const granted = await chrome.permissions.request({ origins: [`${origin}/*`] });
    if (!granted) throw new Error("Без доступа к адресу сервиса расширение не сможет отправлять код");
    const reply = await send({ type: "pair", backendUrl, code });
    if (!reply.ok) throw new Error(reply.error ?? "Не удалось подключить");
    message.className = "ok";
    message.textContent = `Подключено: ${reply.settings?.sellerName ?? ""}. Первая проверка уже идёт.`;
    $<HTMLInputElement>("code").value = "";
    if (reply.settings) render(reply.settings);
  } catch (error) {
    message.className = "bad";
    message.textContent = error instanceof Error ? error.message : String(error);
  }
});

$("run").addEventListener("click", async () => {
  const button = $<HTMLButtonElement>("run");
  button.disabled = true;
  button.textContent = "Проверяем…";
  try {
    const reply = await send({ type: "run" });
    if (reply.settings) render(reply.settings);
  } finally {
    button.disabled = false;
    button.textContent = "Проверить сейчас";
  }
});

$("unpair").addEventListener("click", async () => {
  const reply = await send({ type: "unpair" });
  if (reply.settings) render(reply.settings);
});

void refresh();
setInterval(() => void refresh(), 15_000);
