import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { SendPage } from "../features/supportBot/types";
import { SupportBotJournal } from "./SupportBotJournal";

const page: SendPage = {
  total: 3,
  page: 1,
  page_size: 50,
  counts: { dry_run: 2, failed: 1 },
  sends: [
    {
      id: 3,
      chat_id: "1:aaaa",
      prompt_at: "2026-10-12T06:00:00+00:00",
      nm_id: 1304195061,
      chat_url: "https://seller.wildberries.ru/chat-with-clients?chatId=aaaa",
      seq: 3,
      text: "Здравствуйте! Как дела?",
      status: "dry_run",
      planned_at: "2026-10-13T11:12:00+00:00",
      sent_at: null,
      error: null,
    },
    {
      id: 2,
      chat_id: "1:bbbb",
      prompt_at: "2026-10-11T06:00:00+00:00",
      nm_id: null,
      chat_url: null,
      seq: 1,
      text: "Добрый день!",
      status: "failed",
      planned_at: "2026-10-13T10:00:00+00:00",
      sent_at: null,
      error: "лента не отдала подпись чата",
    },
  ],
};

describe("SupportBotJournal", () => {
  it("показывает счётчики по статусам и общий итог", () => {
    render(<SupportBotJournal page={page} filter={{ status: "", page: 1 }} onFilter={vi.fn()} />);

    expect(screen.getByRole("tab", { name: "Все · 3" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Сухой прогон · 2" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Не ушло · 1" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Отправлено · 0" })).toBeInTheDocument();
  });

  it("строка со ссылкой ведёт в чат WB, а у неудачной отправки вместо текста причина", () => {
    render(<SupportBotJournal page={page} filter={{ status: "", page: 1 }} onFilter={vi.fn()} />);

    const link = screen.getByRole("link", { name: /арт\. 1304195061/ });
    expect(link).toHaveAttribute("href", "https://seller.wildberries.ru/chat-with-clients?chatId=aaaa");
    expect(screen.getByText("лента не отдала подпись чата")).toBeInTheDocument();
  });

  it("выбор статуса возвращает на первую страницу", async () => {
    const onFilter = vi.fn();
    render(<SupportBotJournal page={page} filter={{ status: "", page: 4 }} onFilter={onFilter} />);

    await userEvent.click(screen.getByRole("tab", { name: "Не ушло · 1" }));

    expect(onFilter).toHaveBeenCalledWith({ status: "failed", page: 1 });
  });
});
