import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ManagerConfig, TelegramStatus } from "../features/screenshotControl/types";
import { ScreenshotTelegram, TELEGRAM_POLL_MS } from "./ScreenshotTelegram";

const root = "/api/v1/wb/screenshot-control";
const URL_ = "https://t.me/dbs_shots_bot?start=tok123";

const config: ManagerConfig = {
  mode: "enforce",
  telegram_enabled: true,
  outcomes: [],
  max_upload_bytes: 15 * 1024 * 1024,
  accepted_types: ["image/png"],
  min_side_px: 600,
  extension_url: "/downloads/screenshot-control.zip",
  extension_min_version: "1.0.0",
  bot_username: "dbs_shots_bot",
};

const unlinked: TelegramStatus = { enabled: true, linked: false, username: null, linked_at: null };
const linkedAs = (username: string | null = "ivan_m"): TelegramStatus => ({
  enabled: true,
  linked: true,
  username,
  linked_at: "2026-10-30T09:15:00Z",
});

function json(body: unknown, status = 200, code?: string) {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (code) headers["X-Error-Code"] = code;
  return new Response(JSON.stringify(body), { status, headers });
}

type Handler = (url: string, init?: RequestInit) => Response | undefined;

function renderBlock(handler: Handler, cfg: ManagerConfig | undefined = config) {
  const request = vi.fn(async (url: string, init?: RequestInit) => handler(url, init) ?? json(null, 404));
  vi.stubGlobal("fetch", request);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <ScreenshotTelegram config={cfg} />
    </QueryClientProvider>,
  );
  return request;
}

const calls = (request: ReturnType<typeof renderBlock>, path: string, method = "GET") =>
  request.mock.calls.filter(([url, init]) => url === `${root}${path}` && (init?.method ?? "GET") === method);

/** Отдаёт ссылку с заданным сроком; статус берётся из функции, чтобы его можно было менять по ходу теста. */
function linkFlow(status: () => TelegramStatus, ttlMs = 15 * 60_000): Handler {
  return (url, init) => {
    if (url === `${root}/telegram/link-token` && init?.method === "POST") {
      return json({ url: URL_, expires_at: new Date(Date.now() + ttlMs).toISOString() });
    }
    if (url === `${root}/telegram` && !init?.method) return json(status());
    return undefined;
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("ScreenshotTelegram", () => {
  it("stays a calm «Скоро» without buttons while Telegram is off", () => {
    const request = renderBlock(() => undefined, { ...config, telegram_enabled: false });

    expect(screen.getByRole("heading", { name: "Telegram" })).toBeInTheDocument();
    expect(screen.getByText("Скоро")).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(request).not.toHaveBeenCalled();
  });

  it("falls back to «Скоро» when the server reports the bot as off", async () => {
    renderBlock((url) => (url === `${root}/telegram` ? json({ ...unlinked, enabled: false }) : undefined));

    expect(await screen.findByText("Скоро")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Привязать/ })).not.toBeInTheDocument();
  });

  it("shows loading, then a retry when the status does not load", async () => {
    let fail = true;
    const request = renderBlock((url) =>
      url === `${root}/telegram` ? (fail ? json({ detail: "Сервер недоступен" }, 500) : json(unlinked)) : undefined,
    );

    expect(screen.getByText("Загружаем статус Telegram…")).toBeInTheDocument();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Не удалось загрузить статус Telegram");
    expect(alert).toHaveTextContent("Сервер недоступен");

    fail = false;
    fireEvent.click(within(alert).getByRole("button", { name: "Повторить" }));
    expect(await screen.findByRole("button", { name: "Привязать Telegram" })).toBeInTheDocument();
    expect(calls(request, "/telegram")).toHaveLength(2);
  });

  it("shows how to send screenshots to the bot", async () => {
    renderBlock(linkFlow(() => unlinked));

    expect(await screen.findByRole("heading", { name: "Как отправлять скриншоты в бот" })).toBeInTheDocument();
    expect(screen.getByText(/боту @dbs_shots_bot нативный скриншот чата из приложения WB Partners/)).toBeInTheDocument();
    expect(screen.getByText(/Полож, Доп или Удален/)).toBeInTheDocument();
    expect(screen.getByText(/Бот ответит вердиктом/)).toBeInTheDocument();
  });

  it("issues a link with a countdown, polls the status and reports the binding", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let status = unlinked;
    const request = renderBlock(linkFlow(() => status));

    fireEvent.click(await screen.findByRole("button", { name: "Привязать Telegram" }));

    const open = await screen.findByRole("link", { name: /Открыть в Telegram/ });
    expect(open).toHaveAttribute("href", URL_);
    expect(open).toHaveAttribute("target", "_blank");
    expect(screen.getByText(URL_)).toBeInTheDocument();
    expect(
      screen.getByText("Откройте ссылку в Telegram и нажмите Start. Ссылка действует 15 минут и одноразовая."),
    ).toBeInTheDocument();
    expect(screen.getByRole("timer")).toHaveTextContent(/Действует ещё 1[45]:\d\d/);

    const before = calls(request, "/telegram").length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(TELEGRAM_POLL_MS * 2 + 100);
    });
    expect(calls(request, "/telegram").length).toBeGreaterThanOrEqual(before + 2);
    expect(screen.getByRole("timer")).toHaveTextContent(/Действует ещё 14:5\d/);

    status = linkedAs();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(TELEGRAM_POLL_MS + 100);
    });
    expect(screen.getByRole("status")).toHaveTextContent("Telegram привязан: @ivan_m");
    expect(screen.queryByRole("link", { name: /Открыть в Telegram/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отвязать" })).toBeInTheDocument();

    // Привязка состоялась — опрос остановлен.
    const settled = calls(request, "/telegram").length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(TELEGRAM_POLL_MS * 3);
    });
    expect(calls(request, "/telegram")).toHaveLength(settled);
  });

  it("stops polling when the link expires and offers a new one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const request = renderBlock(linkFlow(() => unlinked, 10_000));

    fireEvent.click(await screen.findByRole("button", { name: "Привязать Telegram" }));
    expect(await screen.findByRole("timer")).toHaveTextContent(/Действует ещё 00:(09|10)/);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(11_000);
    });
    expect(screen.getByRole("timer")).toHaveTextContent("Ссылка истекла.");
    expect(screen.queryByRole("link", { name: /Открыть в Telegram/ })).not.toBeInTheDocument();

    const polled = calls(request, "/telegram").length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(TELEGRAM_POLL_MS * 3);
    });
    expect(calls(request, "/telegram")).toHaveLength(polled);

    fireEvent.click(screen.getByRole("button", { name: "Получить новую ссылку" }));
    expect(await screen.findByRole("link", { name: /Открыть в Telegram/ })).toBeInTheDocument();
    expect(calls(request, "/telegram/link-token", "POST")).toHaveLength(2);
  });

  it("copies the link", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    renderBlock(linkFlow(() => unlinked));

    fireEvent.click(await screen.findByRole("button", { name: "Привязать Telegram" }));
    fireEvent.click(await screen.findByRole("button", { name: "Скопировать ссылку" }));

    expect(writeText).toHaveBeenCalledWith(URL_);
    expect(await screen.findByRole("button", { name: "Скопировано" })).toBeInTheDocument();
  });

  it("explains a 503 telegram_disabled without a pointless retry", async () => {
    renderBlock((url, init) => {
      if (url === `${root}/telegram/link-token` && init?.method === "POST") {
        return json({ detail: "Telegram-бот подключается во втором релизе" }, 503, "telegram_disabled");
      }
      if (url === `${root}/telegram`) return json(unlinked);
      return undefined;
    });

    fireEvent.click(await screen.findByRole("button", { name: "Привязать Telegram" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Привязка Telegram пока недоступна: бот ещё не подключён.");
    expect(within(alert).queryByRole("button", { name: "Повторить" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Открыть в Telegram/ })).not.toBeInTheDocument();
  });

  it("retries a link that failed for another reason", async () => {
    let fail = true;
    renderBlock((url, init) => {
      if (url === `${root}/telegram/link-token` && init?.method === "POST") {
        return fail
          ? json({ detail: "Слишком много запросов" }, 429, "rate_limited")
          : json({ url: URL_, expires_at: new Date(Date.now() + 15 * 60_000).toISOString() });
      }
      if (url === `${root}/telegram`) return json(unlinked);
      return undefined;
    });

    fireEvent.click(await screen.findByRole("button", { name: "Привязать Telegram" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Слишком много запросов подряд. Подождите минуту и повторите.");

    fail = false;
    fireEvent.click(within(alert).getByRole("button", { name: "Повторить" }));
    expect(await screen.findByRole("link", { name: /Открыть в Telegram/ })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows who is linked and unlinks only after confirmation", async () => {
    let status = linkedAs();
    const request = renderBlock((url, init) => {
      if (url === `${root}/telegram` && init?.method === "DELETE") {
        status = unlinked;
        return new Response(null, { status: 204 });
      }
      if (url === `${root}/telegram`) return json(status);
      return undefined;
    });

    const name = await screen.findByText("@ivan_m");
    expect(name.parentElement).toHaveTextContent(/^@ivan_m, привязан 30\.10\.2026, \d\d:15$/);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Отвязать" }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveTextContent("Отвязать Telegram?");
    expect(dialog).toHaveTextContent("Бот перестанет принимать от вас скриншоты.");

    fireEvent.click(within(dialog).getByRole("button", { name: "Отмена" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(calls(request, "/telegram", "DELETE")).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Отвязать" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Отвязать" }));

    expect(await screen.findByRole("button", { name: "Привязать Telegram" })).toBeInTheDocument();
    expect(calls(request, "/telegram", "DELETE")).toHaveLength(1);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByText("@ivan_m")).not.toBeInTheDocument();
  });

  it("keeps the binding and says why when unlinking fails", async () => {
    renderBlock((url, init) => {
      if (url === `${root}/telegram` && init?.method === "DELETE") {
        return json({ detail: "Сервис временно недоступен" }, 503, "limiter_unavailable");
      }
      if (url === `${root}/telegram`) return json(linkedAs());
      return undefined;
    });

    fireEvent.click(await screen.findByRole("button", { name: "Отвязать" }));
    fireEvent.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Отвязать" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Сервис временно не принимает запросы.");
    expect(screen.getByText("@ivan_m")).toBeInTheDocument();
  });

  it("names the account even without a username", async () => {
    renderBlock((url) => (url === `${root}/telegram` ? json(linkedAs(null)) : undefined));

    expect(await screen.findByText("Telegram", { selector: "strong" })).toBeInTheDocument();
  });
});
