import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { periodRange } from "../features/screenshotControl/format";
import { clearImageCache } from "../features/screenshotControl/images";
import type {
  ExtensionInstall,
  ManagerConfig,
  ManagerSettings,
  Submission,
  SubmissionPage,
  Summary,
} from "../features/screenshotControl/types";
import { ScreenshotControlPage } from "./ScreenshotControlPage";

vi.mock("../components/Shell", () => ({ Shell: ({ children }: { children: React.ReactNode }) => <>{children}</> }));

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  clearImageCache();
});

const root = "/api/v1/wb/screenshot-control";

const config: ManagerConfig = {
  mode: "shadow",
  telegram_enabled: false,
  outcomes: [],
  max_upload_bytes: 15 * 1024 * 1024,
  accepted_types: ["image/png", "image/jpeg", "image/webp"],
  min_side_px: 600,
  extension_url: "/downloads/screenshot-control.zip",
  extension_min_version: "1.0.0",
  bot_username: null,
};

const zones = [
  { id: "Europe/Kaliningrad", label: "Калининград, UTC+2", utc_offset_minutes: 120 },
  { id: "Europe/Moscow", label: "Москва, UTC+3", utc_offset_minutes: 180 },
  { id: "Asia/Yekaterinburg", label: "Екатеринбург, UTC+5", utc_offset_minutes: 300 },
];

const settingsOf = (timezone = "Europe/Moscow"): ManagerSettings => ({ timezone, zones, default: "Europe/Moscow" });

function summary(overrides: Partial<Summary> = {}): Summary {
  return {
    period: { from: "2026-10-23", to: "2026-10-23" },
    mode: "enforce",
    preliminary: false,
    counts: { positive: 12, amended: 3, deleted: 2, accepted_total: 17 },
    pending_review: 4,
    awaiting_outcome: 0,
    processing: 0,
    duplicates: 1,
    rejected: 2,
    ...overrides,
  };
}

function submission(id: string, overrides: Partial<Submission> = {}): Submission {
  return {
    id,
    channel: "web",
    received_at: new Date().toISOString(),
    outcome: "positive",
    status: "accepted",
    reason_code: null,
    reason_text: null,
    duplicate_received_at: null,
    fields: { review_at: null, stars: 5, text_preview: null, chat_tail: "x9y8z7" },
    thumb_url: `https://s3.example/thumb/${id}.jpg?sig=1`,
    ...overrides,
  };
}

const page = (items: Submission[], next_cursor: string | null = null): SubmissionPage => ({ items, next_cursor });

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Handler = (url: string, init?: RequestInit) => Response | undefined;

function renderPage(handler: Handler = () => undefined) {
  const request = vi.fn(async (url: string, init?: RequestInit) => {
    const custom = handler(url, init);
    if (custom) return custom;
    if (url === `${root}/config`) return json(config);
    if (url.startsWith(`${root}/summary`)) return json(summary());
    if (url.startsWith(`${root}/submissions`) && init?.method === "POST") return json(submission("new"), 201);
    if (url.startsWith(`${root}/submissions`)) return json(page([]));
    if (url === `${root}/extension/installs`) return json([]);
    if (url === `${root}/settings` && !init?.method) return json(settingsOf());
    return json(null);
  });
  vi.stubGlobal("fetch", request);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ScreenshotControlPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return request;
}

const png = (name: string) => new File([new Uint8Array([137, 80, 78, 71])], name, { type: "image/png" });

const posts = (request: ReturnType<typeof renderPage>) =>
  request.mock.calls.filter(([url, init]) => url === `${root}/submissions` && init?.method === "POST");

const sent = (request: ReturnType<typeof renderPage>) =>
  posts(request).map(([, init]) => {
    const body = init?.body as FormData;
    return { id: body.get("client_submission_id"), outcome: body.get("outcome") };
  });

describe("ScreenshotControlPage", () => {
  it("shows the KPI strip with the not-counted line", async () => {
    renderPage();

    expect(await screen.findByLabelText("Положительные: 12")).toBeInTheDocument();
    expect(screen.getByLabelText("Доп: 3")).toBeInTheDocument();
    expect(screen.getByLabelText("Удалённые: 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Требуют ревью: 4")).toBeInTheDocument();
    expect(screen.getByText("Не засчитано: дубли 1, отклонено 2")).toBeInTheDocument();
    expect(screen.queryByText("Пробный период")).not.toBeInTheDocument();
  });

  it("marks the trial period as preliminary", async () => {
    renderPage((url) =>
      url.startsWith(`${root}/summary`) ? json(summary({ preliminary: true, mode: "shadow" })) : undefined,
    );

    expect(await screen.findByText("Пробный период")).toBeInTheDocument();
    expect(screen.getByText("Цифры предварительные и в KPI пока не идут.")).toBeInTheDocument();
  });

  it("asks the summary for the chosen period", async () => {
    const request = renderPage();
    await screen.findByLabelText("Положительные: 12");

    const day = periodRange("day");
    expect(request.mock.calls.some(([url]) => url === `${root}/summary?from=${day.from}&to=${day.to}`)).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: "Месяц" }));
    const month = periodRange("month");
    await waitFor(() =>
      expect(request.mock.calls.some(([url]) => url === `${root}/summary?from=${month.from}&to=${month.to}`)).toBe(
        true,
      ),
    );

    await userEvent.click(screen.getByRole("button", { name: "Свой" }));
    fireEvent.change(screen.getByLabelText("Начало периода"), { target: { value: "2026-09-01" } });
    fireEvent.change(screen.getByLabelText("Конец периода"), { target: { value: "2026-09-30" } });
    await waitFor(() =>
      expect(request.mock.calls.some(([url]) => url === `${root}/summary?from=2026-09-01&to=2026-09-30`)).toBe(true),
    );

    fireEvent.change(screen.getByLabelText("Начало периода"), { target: { value: "2026-10-05" } });
    expect(screen.getByText("Начало позже конца")).toBeInTheDocument();
  });

  it("shows a summary error with a retry", async () => {
    const request = renderPage((url) =>
      url.startsWith(`${root}/summary`) ? json({ detail: "Период длиннее 366 дней" }, 422) : undefined,
    );

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Не удалось загрузить сводку")).toBeInTheDocument();
    expect(alert).toHaveTextContent("Период длиннее 366 дней");
    const before = request.mock.calls.filter(([url]) => url.startsWith(`${root}/summary`)).length;
    await userEvent.click(within(alert).getByRole("button", { name: "Повторить" }));
    await waitFor(() =>
      expect(request.mock.calls.filter(([url]) => url.startsWith(`${root}/summary`)).length).toBe(before + 1),
    );
  });

  it("does not send a screenshot without an outcome and says so next to the buttons", async () => {
    const request = renderPage();
    await userEvent.upload(screen.getByLabelText("Выбрать файлы"), png("chat.png"));

    await userEvent.click(screen.getByRole("button", { name: "Отправить" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Выберите исход: Полож, Доп или Удален");
    expect(posts(request)).toHaveLength(0);

    await userEvent.click(within(screen.getByRole("group", { name: "Исход" })).getByRole("button", { name: "Полож" }));
    await userEvent.click(screen.getByRole("button", { name: "Отправить" }));

    await waitFor(() => expect(sent(request)).toHaveLength(1));
    expect(sent(request)[0].outcome).toBe("positive");
    expect(await screen.findByText(/Отправлен ·/)).toBeInTheDocument();
  });

  it("applies one outcome to all files and gives each its own id", async () => {
    const request = renderPage();
    await userEvent.upload(screen.getByLabelText("Выбрать файлы"), [png("a.png"), png("b.png"), png("c.png")]);

    await userEvent.click(
      within(screen.getByRole("group", { name: "Исход для всех" })).getByRole("button", { name: "Доп" }),
    );
    await userEvent.click(
      within(screen.getByRole("group", { name: "Исход для c.png" })).getByRole("button", { name: "Удален" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Отправить 3 файла" }));

    await waitFor(() => expect(sent(request)).toHaveLength(3));
    const calls = sent(request);
    expect(calls.map((call) => call.outcome)).toEqual(["amended", "amended", "deleted"]);
    expect(new Set(calls.map((call) => call.id)).size).toBe(3);
    expect(await screen.findByText("Отправлено 3 из 3")).toBeInTheDocument();
  });

  it("retries a failed upload with the same client id", async () => {
    let attempts = 0;
    const request = renderPage((url, init) => {
      if (url === `${root}/submissions` && init?.method === "POST") {
        attempts += 1;
        return attempts === 1
          ? json({ detail: "Слишком много загрузок, подождите минуту" }, 429)
          : json(submission("s"), 201);
      }
      return undefined;
    });
    await userEvent.upload(screen.getByLabelText("Выбрать файлы"), png("chat.png"));
    await userEvent.click(within(screen.getByRole("group", { name: "Исход" })).getByRole("button", { name: "Удален" }));
    await userEvent.click(screen.getByRole("button", { name: "Отправить" }));

    expect(await screen.findByText(/Слишком много загрузок, подождите минуту/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Повторить" }));

    await waitFor(() => expect(sent(request)).toHaveLength(2));
    const [first, second] = sent(request);
    expect(second.id).toBe(first.id);
    expect(await screen.findByText(/Отправлен ·/)).toBeInTheDocument();
  });

  it("takes a screenshot pasted with Ctrl+V", async () => {
    renderPage();
    await screen.findByLabelText("Положительные: 12");

    fireEvent.paste(document, { clipboardData: { files: [png("image.png")] } });

    expect(await screen.findByText("image.png")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отправить" })).toBeEnabled();
  });

  it("refuses a file of the wrong type before sending", async () => {
    renderPage();
    await screen.findByLabelText("Положительные: 12");

    fireEvent.paste(document, { clipboardData: { files: [new File(["x"], "anim.gif", { type: "image/gif" })] } });

    expect(await screen.findByText(/Нужна картинка: PNG, JPEG, WEBP/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Отправить" })).toBeDisabled();
  });

  it("lists submissions with statuses, reasons and thumbnails", async () => {
    renderPage((url, init) =>
      url.startsWith(`${root}/submissions`) && !init?.method
        ? json(
            page([
              submission("s1"),
              submission("s2", { status: "needs_review", outcome: "amended" }),
              submission("s3", {
                status: "duplicate",
                reason_code: "already_uploaded",
                duplicate_received_at: new Date().toISOString(),
              }),
              submission("s4", {
                status: "rejected",
                outcome: "deleted",
                reason_text: "Нужен чёткий скриншот всего окна",
              }),
              submission("s5", { thumb_url: null }),
            ]),
          )
        : undefined,
    );

    const list = await screen.findByRole("region", { name: "Последние отправки" });
    expect(await within(list).findByText("Проверяется", { selector: ".status-badge" })).toBeInTheDocument();
    expect(within(list).getAllByText("Принят", { selector: ".status-badge" })).toHaveLength(2);
    expect(within(list).getByText("Дубль", { selector: ".status-badge" })).toBeInTheDocument();
    expect(within(list).getByText("Не засчитан", { selector: ".status-badge" })).toBeInTheDocument();
    expect(within(list).getByText(/^уже загружен \d\d:\d\d$/)).toBeInTheDocument();
    expect(within(list).getByText("Нужен чёткий скриншот всего окна")).toBeInTheDocument();
    expect(within(list).getAllByText("чат …x9y8z7").length).toBeGreaterThan(0);
    const images = list.querySelectorAll("img");
    expect(images).toHaveLength(4);
    expect(images[0]).toHaveAttribute("src", "https://s3.example/thumb/s1.jpg?sig=1");
  });

  it("filters by status and loads more by cursor", async () => {
    const request = renderPage((url, init) => {
      if (!url.startsWith(`${root}/submissions`) || init?.method) return undefined;
      if (url.includes("cursor=c2")) return json(page([submission("older", { outcome: "deleted" })]));
      return json(page([submission("s1")], "c2"));
    });

    await userEvent.click(await screen.findByRole("button", { name: "Показать ещё" }));
    expect(await screen.findByText(/· Удален/)).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByLabelText("Фильтр по статусу"), "duplicate");
    await waitFor(() =>
      expect(request.mock.calls.some(([url]) => url === `${root}/submissions?status=duplicate&limit=20`)).toBe(true),
    );
  });

  it("polls every five seconds while a screenshot is processing", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let calls = 0;
    renderPage((url, init) => {
      if (!url.startsWith(`${root}/submissions`) || init?.method) return undefined;
      calls += 1;
      return json(page([submission("s1", calls === 1 ? { status: "processing" } : {})]));
    });

    expect(await screen.findByText("В обработке", { selector: ".status-badge" })).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(await screen.findByText("Принят", { selector: ".status-badge" })).toBeInTheDocument();
    const settled = calls;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(calls).toBe(settled);
  });

  it("does not poll screenshots that wait for the manager's outcome", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let lists = 0;
    let summaries = 0;
    renderPage((url, init) => {
      if (url.startsWith(`${root}/summary`)) {
        summaries += 1;
        return json(summary({ awaiting_outcome: 2, processing: 0 }));
      }
      if (!url.startsWith(`${root}/submissions`) || init?.method) return undefined;
      lists += 1;
      return json(page([submission("s1", { status: "awaiting_outcome", outcome: null })]));
    });

    expect(await screen.findByText("Нужен исход", { selector: ".status-badge" })).toBeInTheDocument();
    await screen.findByLabelText("Положительные: 12");
    const before = [lists, summaries];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect([lists, summaries]).toEqual(before);
  });

  it("keeps the thumbnail url while polling re-signs it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let calls = 0;
    renderPage((url, init) => {
      if (!url.startsWith(`${root}/submissions`) || init?.method) return undefined;
      calls += 1;
      return json(
        page([
          submission("s1", {
            status: calls < 3 ? "processing" : "accepted",
            thumb_url: `https://s3.example/thumb/s1.jpg?sig=${calls}`,
          }),
        ]),
      );
    });

    const list = await screen.findByRole("region", { name: "Последние отправки" });
    await within(list).findByText("В обработке", { selector: ".status-badge" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(await within(list).findByText("Принят", { selector: ".status-badge" })).toBeInTheDocument();
    expect(calls).toBeGreaterThanOrEqual(3);
    expect(list.querySelector("img")).toHaveAttribute("src", "https://s3.example/thumb/s1.jpg?sig=1");
  });

  it("takes a fresh thumbnail url once when the old one has expired", async () => {
    let calls = 0;
    renderPage((url, init) => {
      if (!url.startsWith(`${root}/submissions`) || init?.method) return undefined;
      calls += 1;
      return json(page([submission("s1", { thumb_url: `https://s3.example/thumb/s1.jpg?sig=${calls}` })]));
    });

    const list = await screen.findByRole("region", { name: "Последние отправки" });
    await within(list).findByText("Принят", { selector: ".status-badge" });
    fireEvent.error(list.querySelector("img")!);
    await waitFor(() => expect(list.querySelector("img")).toHaveAttribute("src", "https://s3.example/thumb/s1.jpg?sig=2"));
    fireEvent.error(list.querySelector("img")!);
    await waitFor(() => expect(list.querySelector("img")).toBeNull());
    expect(calls).toBe(2);
  });

  it("does not offer a retry for a file the server rejected", async () => {
    renderPage((url, init) =>
      url === `${root}/submissions` && init?.method === "POST"
        ? new Response(JSON.stringify({ detail: "Файл больше 15 МБ" }), {
            status: 413,
            headers: { "Content-Type": "application/json", "X-Error-Code": "file_too_large" },
          })
        : undefined,
    );
    await userEvent.upload(screen.getByLabelText("Выбрать файлы"), png("big.png"));
    await userEvent.click(within(screen.getByRole("group", { name: "Исход" })).getByRole("button", { name: "Полож" }));
    await userEvent.click(screen.getByRole("button", { name: "Отправить" }));

    expect(await screen.findByText(/Файл больше 15 МБ/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Повторить" })).not.toBeInTheDocument();
  });

  it("does not ask for the summary when the module is off", async () => {
    const request = renderPage((url) => (url === `${root}/config` ? json({ ...config, mode: "off" }) : undefined));

    expect(await screen.findByText("Контроль скриншотов выключен")).toBeInTheDocument();
    expect(request.mock.calls.some(([url]) => String(url).startsWith(`${root}/summary`))).toBe(false);
  });

  it("keeps loaded submissions when a background refresh fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let calls = 0;
    renderPage((url, init) => {
      if (!url.startsWith(`${root}/submissions`) || init?.method) return undefined;
      calls += 1;
      if (calls === 1) return json(page([submission("s1", { status: "processing" })]));
      return new Response(JSON.stringify({ detail: "Курсор не подходит" }), {
        status: 422,
        headers: { "Content-Type": "application/json", "X-Error-Code": "bad_cursor" },
      });
    });

    const list = await screen.findByRole("region", { name: "Последние отправки" });
    await within(list).findByText("В обработке", { selector: ".status-badge" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(await within(list).findByText(/не обновилось: Список изменился/)).toBeInTheDocument();
    expect(within(list).getByText("В обработке", { selector: ".status-badge" })).toBeInTheDocument();
  });

  it("lets the manager pick an outcome for a screenshot sent without one", async () => {
    const request = renderPage((url, init) => {
      if (url === `${root}/submissions/s1/outcome`) return json(submission("s1", { status: "processing" }));
      if (url.startsWith(`${root}/submissions`) && !init?.method) {
        return json(page([submission("s1", { status: "awaiting_outcome", outcome: null, channel: "extension" })]));
      }
      return undefined;
    });

    const picker = await screen.findByRole("group", { name: /^Исход для скриншота/ });
    await userEvent.click(within(picker).getByRole("button", { name: "Доп" }));

    const call = request.mock.calls.find(([url]) => url === `${root}/submissions/s1/outcome`);
    expect(call?.[1]?.method).toBe("PATCH");
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ outcome: "amended" });
  });

  it("says when there are no submissions yet", async () => {
    renderPage();

    expect(await screen.findByText("Отправок пока нет")).toBeInTheDocument();
  });

  it("shows a list error with a retry", async () => {
    renderPage((url, init) =>
      url.startsWith(`${root}/submissions`) && !init?.method ? json({ detail: "Сервис недоступен" }, 503) : undefined,
    );

    const list = await screen.findByRole("region", { name: "Последние отправки" });
    const alert = await within(list).findByRole("alert");
    expect(alert).toHaveTextContent("Не удалось загрузить отправки");
    expect(within(alert).getByRole("button", { name: "Повторить" })).toBeInTheDocument();
  });

  it("issues a pairing code with a countdown and revokes an install", async () => {
    const install: ExtensionInstall = {
      id: "inst-1",
      browser: "Chrome 130",
      ext_version: "1.0.2",
      created_at: "2026-10-20T08:00:00Z",
      last_seen_at: "2026-10-23T08:00:00Z",
      last_used_at: null,
    };
    let revoked = false;
    const request = renderPage((url, init) => {
      if (url === `${root}/extension/pairing-code`) {
        return json({ code: "7KQ4M2XZ", expires_at: new Date(Date.now() + 15 * 60_000).toISOString() });
      }
      if (url === `${root}/extension/installs/inst-1` && init?.method === "DELETE") {
        revoked = true;
        return new Response(null, { status: 204 });
      }
      if (url === `${root}/extension/installs`) return json(revoked ? [] : [install]);
      return undefined;
    });

    await userEvent.click(screen.getByRole("tab", { name: "Подключения" }));
    expect(await screen.findByRole("link", { name: /Скачать расширение/ })).toHaveAttribute(
      "href",
      "/downloads/screenshot-control.zip",
    );
    expect(screen.getByText("Скоро")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Получить код/ }));
    expect(await screen.findByText("7KQ4M2XZ")).toBeInTheDocument();
    expect(screen.getByRole("timer")).toHaveTextContent(/Действует ещё 1[45]:\d\d/);

    await userEvent.click(await screen.findByRole("button", { name: /^Отозвать/ }));
    expect(
      request.mock.calls.some(
        ([url, init]) => url === `${root}/extension/installs/inst-1` && init?.method === "DELETE",
      ),
    ).toBe(true);
    expect(await screen.findByText("Расширение ещё не подключено ни в одном браузере.")).toBeInTheDocument();
  });

  it("shows an expired pairing code", async () => {
    renderPage((url) =>
      url === `${root}/extension/pairing-code`
        ? json({ code: "7KQ4M2XZ", expires_at: new Date(Date.now() - 1000).toISOString() })
        : undefined,
    );

    await userEvent.click(screen.getByRole("tab", { name: "Подключения" }));
    await userEvent.click(await screen.findByRole("button", { name: /Получить код/ }));

    expect(await screen.findByText("Код истёк. Получите новый.")).toBeInTheDocument();
  });
  it("says which timezone the review time is read in and leads to the setting", async () => {
    renderPage();

    const hint = await screen.findByText(/Время отзыва читается в вашем поясе: Москва, UTC\+3\./);
    await userEvent.click(within(hint).getByRole("button", { name: "Изменить" }));

    expect(screen.getByRole("tab", { name: "Подключения" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("heading", { name: "Часовой пояс" })).toBeInTheDocument();
    expect(screen.getByText("Укажите пояс, в котором кабинет WB показывает вам время отзыва.")).toBeInTheDocument();
    expect(screen.getByLabelText("Пояс")).toHaveValue("Europe/Moscow");
  });

  it("saves the chosen timezone with PUT and updates the hint", async () => {
    const request = renderPage((url, init) =>
      url === `${root}/settings` && init?.method === "PUT"
        ? json(settingsOf(JSON.parse(String(init.body)).timezone))
        : undefined,
    );

    await userEvent.click(screen.getByRole("tab", { name: "Подключения" }));
    const select = await screen.findByLabelText("Пояс");
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
    await userEvent.selectOptions(select, "Asia/Yekaterinburg");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Сохранено");
    const puts = request.mock.calls.filter(([url, init]) => url === `${root}/settings` && init?.method === "PUT");
    expect(puts).toHaveLength(1);
    expect(JSON.parse(String(puts[0][1]?.body))).toEqual({ timezone: "Asia/Yekaterinburg" });

    await userEvent.click(screen.getByRole("tab", { name: "Мои скриншоты" }));
    expect(await screen.findByText(/читается в вашем поясе: Екатеринбург, UTC\+5\./)).toBeInTheDocument();
  });

  it("shows why the timezone was not saved", async () => {
    renderPage((url, init) =>
      url === `${root}/settings` && init?.method === "PUT"
        ? new Response(JSON.stringify({ detail: "Такого часового пояса нет в списке" }), {
            status: 422,
            headers: { "Content-Type": "application/json", "X-Error-Code": "bad_timezone" },
          })
        : undefined,
    );

    await userEvent.click(screen.getByRole("tab", { name: "Подключения" }));
    await userEvent.selectOptions(await screen.findByLabelText("Пояс"), "Europe/Kaliningrad");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Такого часового пояса нет в списке");
  });

  it("offers a retry when the timezone does not load", async () => {
    renderPage((url) => (url === `${root}/settings` ? json({ detail: "Сервер недоступен" }, 503) : undefined));

    await userEvent.click(screen.getByRole("tab", { name: "Подключения" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Не удалось загрузить часовой пояс");
    expect(within(alert).getByRole("button", { name: "Повторить" })).toBeInTheDocument();
    expect(screen.queryByText(/читается в вашем поясе/)).not.toBeInTheDocument();
  });
});
