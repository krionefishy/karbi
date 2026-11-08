import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { DailyPoint } from "../features/supportBot/types";
import { DailyBars, RateLines } from "./SupportBotRepliesCharts";

const day = (date: string, patch: Partial<DailyPoint> = {}): DailyPoint => ({
  date,
  started_live: 0,
  started_control: 0,
  eligible48_live: 0,
  eligible48_control: 0,
  replied48_live: 0,
  replied48_control: 0,
  replies_live: 0,
  replies_control: 0,
  ...patch,
});

const daily = [
  day("2026-10-25", {
    started_live: 20,
    started_control: 10,
    eligible48_live: 20,
    eligible48_control: 10,
    replied48_live: 12,
    replied48_control: 4,
    replies_live: 7,
    replies_control: 3,
  }),
  day("2026-10-26"),
  day("2026-11-08", { started_live: 5, eligible48_live: 5, replies_live: 2 }),
];
const GENERATED = "2026-11-08T12:00:00Z";

const days = () => screen.getAllByRole("button");

describe("подсказки графика «Ответы по дням»", () => {
  it("наведение показывает дату, числа и накопленный итог, уход скрывает", async () => {
    const user = userEvent.setup();
    render(<DailyBars daily={daily} liveSince={null} generatedAt={GENERATED} />);
    expect(screen.queryByRole("tooltip")).toBeNull();
    await user.hover(days()[0]);
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent("25.10");
    expect(tip).toHaveTextContent("С напоминанием");
    expect(tip).toHaveTextContent("Накопленный итог10");
    expect(tip).toHaveTextContent("12 из 20 · 60,0%");
    await user.unhover(days()[0]);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("пустой день: «нет данных», а не ноль", async () => {
    const user = userEvent.setup();
    render(<DailyBars daily={daily} liveSince={null} generatedAt={GENERATED} />);
    await user.hover(days()[1]);
    expect(screen.getByRole("tooltip")).toHaveTextContent("нет данных");
  });

  it("клавиатура: фокус показывает, Enter закрепляет, Esc убирает", async () => {
    const user = userEvent.setup();
    render(<DailyBars daily={daily} liveSince={null} generatedAt={GENERATED} />);
    await user.tab();
    expect(days()[0]).toHaveFocus();
    expect(days()[0]).toHaveAccessibleName(/25\.10.*Накопленный итог: 10/);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    await user.keyboard("{Enter}");
    await user.tab();
    expect(screen.getByRole("tooltip")).toHaveTextContent("25.10");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("касание закрепляет подсказку, повторное и вне графика убирает", async () => {
    const user = userEvent.setup();
    render(
      <div>
        <button type="button">снаружи</button>
        <DailyBars daily={daily} liveSince={null} generatedAt={GENERATED} />
      </div>,
    );
    const first = days().find((el) => el.getAttribute("aria-label")?.includes("25.10"))!;
    await user.click(first);
    await user.unhover(first);
    expect(screen.getByRole("tooltip")).toHaveTextContent("25.10");
    await user.click(first);
    expect(screen.queryByRole("tooltip")).toBeNull();
    await user.click(first);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByText("снаружи"));
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});

describe("подсказки графика «Доля ответивших за 48 ч»", () => {
  it("проценты, число диалогов, интервал и разница", async () => {
    const user = userEvent.setup();
    render(<RateLines daily={daily} liveSince={null} generatedAt={GENERATED} />);
    await user.hover(days()[0]);
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent("12 из 20 · 60,0%");
    expect(tip).toHaveTextContent("4 из 10 · 40,0%");
    expect(tip).toHaveTextContent("+20,0 п. п.");
    expect(tip).toHaveTextContent("95%:");
  });

  it("день без стартов и день с незакрытым окном", async () => {
    const user = userEvent.setup();
    render(<RateLines daily={daily} liveSince={null} generatedAt={GENERATED} />);
    await user.hover(days()[1]);
    expect(screen.getByRole("tooltip")).toHaveTextContent("нет данных");
    await user.hover(days()[2]);
    expect(screen.getByRole("tooltip")).toHaveTextContent("окно 48 ч ещё не закрыто");
  });
});
