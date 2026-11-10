import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Pace } from "../features/supportBot/types";
import { SupportBotPace } from "./SupportBotPace";

const DEFAULTS: Pace = { interval_minutes: 60, jitter_min_minutes: 1, jitter_max_minutes: 10 };

function setup(overrides: Partial<React.ComponentProps<typeof SupportBotPace>> = {}) {
  const onSave = vi.fn();
  render(
    <SupportBotPace
      pace={DEFAULTS}
      defaults={DEFAULTS}
      custom={false}
      busy={false}
      error=""
      rescheduled={null}
      onSave={onSave}
      {...overrides}
    />,
  );
  return onSave;
}

function type(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

describe("SupportBotPace", () => {
  it("shows the shared values in grey and a live example", () => {
    setup();
    expect(screen.getByText("Общее: 60")).toBeInTheDocument();
    expect(screen.getByText(/например, через 1 ч 1–10 мин/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
  });

  it("updates the example while typing and warns about the recalculation", () => {
    setup();
    type("Интервал, мин", "120");
    type("Добавка до, мин", "3");
    expect(screen.getByText(/например, через 2 ч 1–3 мин/)).toBeInTheDocument();
    expect(screen.getByText(/расписание уже идущих диалогов будет пересчитано/)).toBeInTheDocument();
  });

  it("saves own values", () => {
    const onSave = setup();
    type("Интервал, мин", "120");
    type("Добавка до, мин", "3");
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(onSave).toHaveBeenCalledWith({ interval_minutes: 120, jitter_min_minutes: null, jitter_max_minutes: 3 });
  });

  it("blocks values the server would refuse", () => {
    const onSave = setup();
    type("Интервал, мин", "5");
    expect(screen.getByRole("alert")).toHaveTextContent("от 10 до 1440");
    expect(screen.getByRole("button", { name: "Сохранить" })).toBeDisabled();
    type("Интервал, мин", "60");
    type("Добавка от, мин", "20");
    expect(screen.getByRole("alert")).toHaveTextContent("меньше добавки «от»");
    type("Добавка до, мин", "300");
    expect(screen.getByRole("alert")).toHaveTextContent("больше 240");
    expect(onSave).not.toHaveBeenCalled();
  });

  it("resets to the shared values", () => {
    const onSave = setup({ pace: { interval_minutes: 120, jitter_min_minutes: 1, jitter_max_minutes: 3 }, custom: true });
    expect(screen.getByText("Свои значения кабинета")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Сбросить к общему" }));
    expect(onSave).toHaveBeenCalledWith({ interval_minutes: null, jitter_min_minutes: null, jitter_max_minutes: null });
  });

  it("shows the server error and the result of a save", () => {
    setup({ error: "Добавка «до» не может быть меньше добавки «от»" });
    expect(screen.getByRole("alert")).toHaveTextContent("не может быть меньше");
  });

  it("reports how many dialogs were recalculated", () => {
    setup({ rescheduled: 12 });
    expect(screen.getByRole("status")).toHaveTextContent("Пересчитано диалогов: 12");
  });
});
