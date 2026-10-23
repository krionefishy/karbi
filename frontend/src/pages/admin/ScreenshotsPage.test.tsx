import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AdminQueuePage,
  AdminSubmission,
  Managers,
  Match,
  Pipeline,
} from "../../features/screenshotControl/types";
import { ScreenshotsPage } from "./ScreenshotsPage";

vi.mock("../../components/Shell", () => ({ Shell: ({ children }: { children: React.ReactNode }) => <>{children}</> }));

afterEach(() => {
  vi.unstubAllGlobals();
});

const adminRoot = "/api/v1/admin/screenshot-control";

const match = (other_id: string, kind: Match["kind"], score: number): Match => ({
  other_id,
  other_user: { id: "u-anna", username: "Анна" },
  other_received_at: "2026-10-22T07:55:00Z",
  other_outcome: "deleted",
  other_verdict: "accepted",
  kind,
  score,
  thumb_url: "https://s3.example/thumb/first.jpg",
});

function submission(id: string, username: string, overrides: Partial<AdminSubmission> = {}): AdminSubmission {
  return {
    id,
    user: { id: `u-${id}`, username },
    channel: "extension",
    received_at: "2026-10-22T08:15:00Z",
    outcome: "positive",
    outcome_source: "button",
    caption_raw: null,
    stage: "decided",
    verdict: "needs_review",
    auto_verdict: "needs_review",
    reasons: [{ code: "extraction_disagreement_time", severity: "warn", detail: null, other_submission_id: null }],
    suspicion: "medium",
    duplicate_of: null,
    audit_requested: false,
    matches: [],
    fields: {
      layout: "desktop_window",
      case_kind: "desktop",
      chat_id: "1b7c2d9a-fc50b40e0e90",
      chat_id_source: "url",
      key_time: "2026-10-22T03:39:00Z",
      key_time_precision: "minute",
      review_at: "2026-10-22T03:39:00Z",
      review_text: "Не работает.",
      review_text_truncated: false,
      stars: 1,
      article: null,
      client_messages: [],
      buyer_name_seen: true,
      quality: null,
      confidence: { chat_id: 0.99, time: 0.95, text: 0.78 },
    },
    agreement: { chat_id: 1, time: 0.96, text: 0.9 },
    thumb_url: `https://s3.example/thumb/${id}.jpg`,
    ...overrides,
  };
}

const counts = { review: 7, duplicates: 3, audit: 5 };
const page = (items: AdminSubmission[]): AdminQueuePage => ({ items, next_cursor: null, counts });

const managers: Managers = {
  period: { from: "2026-10-01", to: "2026-10-23" },
  items: [
    {
      user: { id: "u-igor", username: "Игорь" },
      total: 44,
      accepted: 30,
      positive: 12,
      amended: 7,
      deleted: 11,
      duplicate: 9,
      needs_review: 5,
      rejected: 0,
      processing: 0,
      duplicate_rate: 0.2,
      review_rate: 0.11,
      reject_rate: 0,
      last_submission_at: null,
      flags: ["high_duplicate_rate"],
    },
  ],
};

const pipeline: Pipeline = {
  mode: "shadow",
  telegram_enabled: false,
  stages: { processing: 2, extracted: 0 },
  oldest_processing_age_s: 8,
  p50_s: 9,
  p95_s: 24,
  vlm_error_rate_1h: 0.31,
  extraction_failed_today: 1,
  tokens_today: 412000,
  storage: { objects: { stored: 1240 }, stored_bytes: 1_700_000_000, oldest_staged_age_s: 4, staging_used_percent: 61 },
  channels_today: { extension: 30, telegram: 0, web: 4 },
};

function json(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

type Handler = (url: string, init?: RequestInit) => Response | undefined;

function renderPage(queue: Partial<Record<string, AdminSubmission[]>> = {}, handler: Handler = () => undefined) {
  const request = vi.fn(async (url: string, init?: RequestInit) => {
    const custom = handler(url, init);
    if (custom) return custom;
    const parsed = new URL(url, "http://x");
    if (parsed.pathname === `${adminRoot}/queue`) {
      return json(page(queue[parsed.searchParams.get("kind") ?? "review"] ?? []));
    }
    if (parsed.pathname.endsWith("/image-url")) {
      return json({ url: `https://s3.example/${parsed.searchParams.get("kind")}.jpg`, expires_at: null, available: true });
    }
    if (parsed.pathname.endsWith("/resolve")) return json({});
    if (parsed.pathname === `${adminRoot}/managers`) return json(managers);
    if (parsed.pathname === `${adminRoot}/pipeline`) return json(pipeline);
    return json({}, 404);
  });
  vi.stubGlobal("fetch", request);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ScreenshotsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return request;
}

function resolveCalls(request: ReturnType<typeof renderPage>) {
  return request.mock.calls
    .filter(([url]) => String(url).endsWith("/resolve"))
    .map(([url, init]) => ({ url: String(url), body: JSON.parse(String(init?.body)) }));
}

const anna = submission("s-anna", "Анна", {
  suspicion: "high",
  reasons: [{ code: "cross_user_duplicate", severity: "high", detail: null, other_submission_id: "first" }],
});
const igor = submission("s-igor", "Игорь");
const oleg = submission("s-oleg", "Олег", { suspicion: "low" });

const card = () => screen.getByRole("region", { name: /^Скриншот/ });

describe("ScreenshotsPage", () => {
  it("shows tab counters and the queue with risk badges", async () => {
    renderPage({ review: [anna, igor] });

    expect(await screen.findByRole("tab", { name: "Проверка 7" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Подозрения 3" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Аудит 5" })).toBeInTheDocument();
    const queue = screen.getByRole("list", { name: "Очередь" });
    expect(within(queue).getByText("тот же отзыв уже прислал другой менеджер")).toBeInTheDocument();
    expect(within(queue).getByText("Высокий")).toBeInTheDocument();
    expect(within(queue).getByText("Средний")).toBeInTheDocument();
    expect(within(card()).getByText("…fc50b40e0e90")).toBeInTheDocument();
    expect(within(card()).getAllByText("VLM = OCR")).toHaveLength(2);
    expect(within(card()).getByText("0,78")).toBeInTheDocument();
  });

  it("invites rather than apologises when the queue is empty", async () => {
    renderPage();
    expect(await screen.findByText("Очередь пуста")).toBeInTheDocument();
  });

  it("approves with the button and moves to the next screenshot", async () => {
    const user = userEvent.setup();
    const request = renderPage({ review: [anna, igor] });
    await screen.findByRole("region", { name: "Скриншот Анна" });

    await user.click(within(card()).getByRole("button", { name: /Засчитать/ }));

    await waitFor(() => expect(resolveCalls(request)).toHaveLength(1));
    expect(resolveCalls(request)[0]).toEqual({
      url: `${adminRoot}/submissions/s-anna/resolve`,
      body: { decision: "approve", note: "" },
    });
    expect(await screen.findByRole("region", { name: "Скриншот Игорь" })).toBeInTheDocument();
  });

  it("does not decline without a comment", async () => {
    const user = userEvent.setup();
    const request = renderPage({ review: [anna, igor] });
    await screen.findByRole("region", { name: "Скриншот Анна" });

    await user.click(within(card()).getByRole("button", { name: /Отклонить/ }));
    await user.click(within(card()).getByRole("button", { name: "Отклонить" }));
    expect(within(card()).getByRole("alert")).toHaveTextContent("Напишите, почему не засчитан");
    expect(resolveCalls(request)).toHaveLength(0);

    await user.type(within(card()).getByLabelText("Почему не засчитан"), "чужой кейс");
    await user.click(within(card()).getByRole("button", { name: "Отклонить" }));

    await waitFor(() => expect(resolveCalls(request)).toHaveLength(1));
    expect(resolveCalls(request)[0].body).toEqual({ decision: "decline", note: "чужой кейс" });
  });

  it("explains note_required from the API next to the field", async () => {
    const user = userEvent.setup();
    renderPage({ review: [anna] }, (url) =>
      url.endsWith("/resolve")
        ? json({ detail: "note required" }, 422, { "X-Error-Code": "note_required" })
        : undefined,
    );
    await screen.findByRole("region", { name: "Скриншот Анна" });

    await user.click(within(card()).getByRole("button", { name: /Отклонить/ }));
    await user.type(within(card()).getByLabelText("Почему не засчитан"), " x ");
    await user.click(within(card()).getByRole("button", { name: "Отклонить" }));

    expect(await within(card()).findByRole("alert")).toHaveTextContent("Без комментария это решение не принять");
  });

  it("works from the keyboard in the Russian layout and ignores keys while typing", async () => {
    const request = renderPage({ review: [anna, igor, oleg] });
    await screen.findByRole("region", { name: "Скриншот Анна" });

    fireEvent.keyDown(window, { code: "ArrowRight", key: "ArrowRight" });
    expect(await screen.findByRole("region", { name: "Скриншот Игорь" })).toBeInTheDocument();

    fireEvent.keyDown(window, { code: "KeyD", key: "в" });
    const note = await within(card()).findByLabelText("Почему не засчитан");
    expect(note).toHaveFocus();
    fireEvent.keyDown(note, { code: "KeyA", key: "ф" });
    expect(resolveCalls(request)).toHaveLength(0);

    fireEvent.click(within(card()).getByRole("button", { name: "Отмена" }));
    fireEvent.keyDown(window, { code: "KeyA", key: "ф" });

    await waitFor(() => expect(resolveCalls(request)).toHaveLength(1));
    expect(resolveCalls(request)[0]).toEqual({
      url: `${adminRoot}/submissions/s-igor/resolve`,
      body: { decision: "approve", note: "" },
    });
    expect(await screen.findByRole("region", { name: "Скриншот Олег" })).toBeInTheDocument();
  });

  it("marks a duplicate of the chosen match with N", async () => {
    const user = userEvent.setup();
    const withMatch = submission("s-igor", "Игорь", { matches: [match("first", "case_key", 1)] });
    const request = renderPage({ review: [withMatch] });
    await screen.findByRole("region", { name: "Скриншот Игорь" });

    fireEvent.keyDown(window, { code: "KeyN", key: "т" });
    const radio = await within(card()).findByRole("radio");
    expect(radio).toBeChecked();
    await user.click(within(card()).getByRole("button", { name: "Отметить дублем" }));

    await waitFor(() => expect(resolveCalls(request)).toHaveLength(1));
    expect(resolveCalls(request)[0].body).toEqual({ decision: "duplicate_of", duplicate_of: "first", note: "" });
  });

  it("compares a duplicate with the first screenshot and clears it", async () => {
    const user = userEvent.setup();
    const duplicate = submission("s-dup", "Игорь", {
      verdict: "duplicate",
      suspicion: "high",
      duplicate_of: "first",
      reasons: [{ code: "cross_user_duplicate", severity: "high", detail: null, other_submission_id: "first" }],
      matches: [match("first", "case_key", 1), match("first", "chat_text", 0.97), match("first", "near_image", 0.91)],
    });
    const request = renderPage({ duplicates: [duplicate] });
    await user.click(await screen.findByRole("tab", { name: /Подозрения/ }));
    await screen.findByRole("region", { name: "Скриншот Игорь" });

    expect(within(card()).getByRole("button", { name: "Сравнение с первым скрином" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    expect(within(card()).getByText(/Первый: Анна/)).toBeInTheDocument();
    expect(within(card()).getByText("совпал")).toBeInTheDocument();
    expect(within(card()).getByText("0,97")).toBeInTheDocument();
    expect(within(card()).getByText("похожа, 0,91")).toBeInTheDocument();
    expect(within(card()).getByText("Анна: Удален · Игорь: Полож")).toBeInTheDocument();

    await user.click(within(card()).getByRole("button", { name: "Не дубль, засчитать" }));
    await waitFor(() => expect(resolveCalls(request)).toHaveLength(1));
    expect(resolveCalls(request)[0].body).toEqual({ decision: "not_duplicate", note: "" });
  });

  it("asks for a comment before taking back an accepted audit sample as a duplicate", async () => {
    const user = userEvent.setup();
    const audited = submission("s-audit", "Олег", {
      verdict: "accepted",
      audit_requested: true,
      suspicion: "low",
      reasons: [{ code: "audit_sample", severity: "info", detail: null, other_submission_id: null }],
      matches: [match("first", "chat_text", 0.95)],
    });
    const request = renderPage({ audit: [audited] });
    await user.click(await screen.findByRole("tab", { name: /Аудит/ }));
    await screen.findByRole("region", { name: "Скриншот Олег" });

    await user.click(within(card()).getByRole("button", { name: "Сравнение с первым скрином" }));
    await user.click(within(card()).getByRole("button", { name: "Дубль, не засчитывать" }));
    await user.click(within(card()).getByRole("button", { name: "Отметить дублем" }));
    expect(within(card()).getByRole("alert")).toHaveTextContent("Напишите, почему снимаете засчитанный");
    expect(resolveCalls(request)).toHaveLength(0);

    await user.click(within(card()).getByRole("button", { name: "Отмена" }));
    await user.click(within(card()).getByRole("button", { name: /Подтвердить/ }));
    await waitFor(() => expect(resolveCalls(request)).toHaveLength(1));
    expect(resolveCalls(request)[0].body).toEqual({ decision: "audit_ok", note: "" });
  });

  it("shows manager signals and pipeline warnings on their tabs", async () => {
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("tab", { name: "Менеджеры" }));
    expect(await screen.findByText("Дублей 20%")).toBeInTheDocument();
    expect(screen.getByText("Сигналы для разговора, а не санкции")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Выгрузить CSV" })).toBeEnabled();

    await user.click(screen.getByRole("tab", { name: "Конвейер" }));
    expect(await screen.findByText("Ошибки распознавания за час: 31%")).toBeInTheDocument();
    expect(screen.getByText("24 с")).toBeInTheDocument();
    expect(screen.getByText("Пробный")).toBeInTheDocument();
  });

  it("offers a retry when the queue fails to load", async () => {
    const user = userEvent.setup();
    let fail = true;
    renderPage({ review: [anna] }, (url) =>
      url.includes("/queue?kind=review&limit=50") && fail ? json({ detail: "Сервис недоступен" }, 503) : undefined,
    );

    expect(await screen.findByText("Очередь не загрузилась")).toBeInTheDocument();
    fail = false;
    await user.click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByRole("region", { name: "Скриншот Анна" })).toBeInTheDocument();
  });
});
