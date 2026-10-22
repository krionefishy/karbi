import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CardDocumentItem, CardDocuments } from "../features/cardDocuments/types";
import { CardDocumentsPage } from "./CardDocumentsPage";

vi.mock("../components/Shell", () => ({ Shell: ({ children }: { children: React.ReactNode }) => <>{children}</> }));

afterEach(() => {
  vi.unstubAllGlobals();
});

const seller = { id: "seller-1", name: "ИП Мунаева" };

function item(nmId: number, overrides: Partial<CardDocumentItem> = {}): CardDocumentItem {
  return {
    nm_id: nmId,
    vendor_code: `SKU-${nmId}`,
    title: `Товар ${nmId}`,
    subject_name: "Воздуходувки",
    state: "ok",
    reason: "valid",
    document_number: `RU Д-${nmId}`,
    document_start: "2025-01-10",
    document_end: "2027-01-09",
    days_left: 444,
    checked_at: "2026-10-22T07:00:00Z",
    since: null,
    ...overrides,
  };
}

const sample: CardDocuments = {
  collected_at: "2026-10-22T07:00:00Z",
  last_error: null,
  stale: false,
  summary: { total: 3, ok: 1, rejected: 1, missing: 0, expiring: 1, mismatch: 0 },
  items: [
    item(1504904633, { state: "rejected", reason: "document_not_found", since: "2026-10-21T09:00:00Z" }),
    item(1175671003, { state: "expiring", reason: "valid", days_left: 6 }),
    item(42),
  ],
  notices: [],
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderPage(
  documents: CardDocuments | Response,
  sellers: unknown[] = [seller],
  invite: Response = json({ url: "https://t.me/docs_bot?start=abc", expires_at: "2026-10-23T07:00:00Z" }),
) {
  const request = vi.fn(async (url: string, _init?: RequestInit) => {
    if (url.endsWith("/invite-link")) return invite;
    if (url.includes("/automations/wb-card-documents/sellers")) return json(sellers);
    if (url.endsWith("/refresh")) return json(null);
    if (url.includes("/wb/card-documents/sellers/")) return documents instanceof Response ? documents : json(documents);
    return json([]);
  });
  vi.stubGlobal("fetch", request);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CardDocumentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return request;
}

describe("CardDocumentsPage", () => {
  it("shows problem cards first with Russian reasons and filters by tile", async () => {
    renderPage(sample);

    expect(await screen.findByText("Документ не найден в реестре")).toBeInTheDocument();
    expect(screen.getByText("осталось 6 дн.")).toBeInTheDocument();
    expect(screen.queryByText("Товар 42")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "1504904633" })).toHaveAttribute(
      "href",
      "https://www.wildberries.ru/catalog/1504904633/detail.aspx",
    );

    await userEvent.click(screen.getByRole("button", { name: /В порядке/ }));
    expect(screen.getByText("Товар 42")).toBeInTheDocument();
    expect(screen.queryByText("Товар 1504904633")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /В порядке/ }));
    expect(screen.queryByText("Товар 42")).not.toBeInTheDocument();
  });

  it("copies the nmID", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    renderPage(sample);

    await user.click(await screen.findByRole("button", { name: "Скопировать nmID 1504904633" }));

    expect(writeText).toHaveBeenCalledWith("1504904633");
    expect(await screen.findByRole("button", { name: "nmID 1504904633 скопирован" })).toBeInTheDocument();
  });

  it("stacks backend notices as banners by level", async () => {
    renderPage({
      ...sample,
      notices: [
        { kind: "stale", level: "warning", since: "2026-10-22T07:00:00Z", detail: null },
        { kind: "collection_error", level: "danger", since: null, detail: "WB ответил 401" },
        { kind: "no_subscribers", level: "warning", since: null, detail: null },
      ],
    });

    expect(await screen.findByText("Наблюдение не работает")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("WB ответил 401");
    expect(screen.getByText(/Нажмите «Ссылка на бота»/)).toBeInTheDocument();
  });

  it("ignores legacy stale and last_error without notices", async () => {
    renderPage({ ...sample, stale: true, last_error: "WB ответил 401" });

    expect(await screen.findByText("Документ не найден в реестре")).toBeInTheDocument();
    expect(screen.queryByText("Наблюдение не работает")).not.toBeInTheDocument();
    expect(screen.queryByText(/WB ответил 401/)).not.toBeInTheDocument();
  });

  it("says the cabinet was never collected", async () => {
    renderPage({ ...sample, collected_at: null, summary: { ...sample.summary, total: 0 }, items: [] });

    expect(await screen.findByText("Документы ещё не проверяли")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Обновить данные/ })).toBeEnabled();
  });

  it("tells when there are no problems", async () => {
    renderPage({
      ...sample,
      summary: { total: 1, ok: 1, rejected: 0, missing: 0, expiring: 0, mismatch: 0 },
      items: [item(42)],
    });

    expect(await screen.findByText(/Проблем нет/)).toBeInTheDocument();
  });

  it("shows a load error with a retry", async () => {
    renderPage(json({ detail: "Сервис недоступен" }, 503));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Не удалось загрузить документы карточек")).toBeInTheDocument();
    expect(within(alert).getByRole("button", { name: "Повторить" })).toBeInTheDocument();
  });

  it("asks to connect a seller when none is enrolled", async () => {
    renderPage(sample, []);

    expect(await screen.findByText("Подключите первого селлера")).toBeInTheDocument();
  });

  it("starts a refresh", async () => {
    const request = renderPage(sample);

    await userEvent.click(await screen.findByRole("button", { name: /Обновить данные/ }));

    expect(
      request.mock.calls.some(
        ([url, init]) => url === "/api/v1/wb/card-documents/sellers/seller-1/refresh" && init?.method === "POST",
      ),
    ).toBe(true);
  });

  it("issues a bot link with its expiry and copies it", async () => {
    const user = userEvent.setup();
    const writeText = vi.spyOn(navigator.clipboard, "writeText");
    const request = renderPage(sample);

    await user.click(await screen.findByRole("button", { name: /Ссылка на бота/ }));

    expect(await screen.findByText("https://t.me/docs_bot?start=abc")).toBeInTheDocument();
    expect(screen.getByText(/Действует до/)).toBeInTheDocument();
    expect(
      request.mock.calls.some(
        ([url, init]) => url === "/api/v1/wb/card-documents/sellers/seller-1/invite-link" && init?.method === "POST",
      ),
    ).toBe(true);

    await user.click(screen.getByRole("button", { name: /Копировать/ }));
    expect(writeText).toHaveBeenCalledWith("https://t.me/docs_bot?start=abc");
    expect(screen.getByRole("button", { name: /Скопировано/ })).toBeInTheDocument();
  });

  it("explains that the bot is not registered yet", async () => {
    renderPage(sample, [seller], json({ detail: "bot not registered" }, 409));

    await userEvent.click(await screen.findByRole("button", { name: /Ссылка на бота/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("ещё не зарегистрирован — обратитесь к администратору");
  });

  it("reports any other invite failure", async () => {
    renderPage(sample, [seller], json({ detail: "Сервис недоступен" }, 500));

    await userEvent.click(await screen.findByRole("button", { name: /Ссылка на бота/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось выпустить ссылку: Сервис недоступен");
  });
});
