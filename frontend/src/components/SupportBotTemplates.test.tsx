import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SupportBotTemplates } from "./SupportBotTemplates";

const templates = [
  { id: 1, text: "Здравствуйте! Как дела с товаром?", active: true },
  { id: 2, text: "Добрый день! Мы на связи.", active: false },
];

function setup() {
  const handlers = { onAdd: vi.fn(), onSave: vi.fn(), onToggle: vi.fn(), onDelete: vi.fn() };
  render(<SupportBotTemplates templates={templates} busy={false} {...handlers} />);
  return handlers;
}

describe("SupportBotTemplates", () => {
  it("клик по тексту открывает правку на месте и сохраняет изменённый текст", async () => {
    const handlers = setup();
    await userEvent.click(screen.getByText("Здравствуйте! Как дела с товаром?"));
    const field = screen.getByLabelText("Текст напоминания");
    await userEvent.clear(field);
    await userEvent.type(field, "Новый текст");
    await userEvent.click(screen.getByRole("button", { name: /Сохранить/ }));

    expect(handlers.onSave).toHaveBeenCalledWith(1, "Новый текст");
  });

  it("тумблер включает и выключает текст, у отключённого есть пометка", async () => {
    const handlers = setup();
    const switches = screen.getAllByRole("switch");
    await userEvent.click(switches[0]);
    await userEvent.click(switches[1]);

    expect(handlers.onToggle).toHaveBeenNthCalledWith(1, 1, false);
    expect(handlers.onToggle).toHaveBeenNthCalledWith(2, 2, true);
    expect(screen.getByText(/отключён: бот его не использует/)).toBeInTheDocument();
  });

  it("удаление требует подтверждения", async () => {
    const handlers = setup();
    await userEvent.click(screen.getAllByLabelText("Удалить текст")[0]);
    expect(handlers.onDelete).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Удалить" }));

    expect(handlers.onDelete).toHaveBeenCalledWith(1);
  });

  it("пустой текст и текст длиннее потолка WB сохранить нельзя", async () => {
    const handlers = setup();
    await userEvent.click(screen.getByText("Добавить текст"));
    const save = screen.getByRole("button", { name: /Добавить/ });
    expect(save).toBeDisabled();
    await userEvent.click(screen.getByLabelText("Текст напоминания"));
    await userEvent.paste("а".repeat(1001));

    expect(screen.getByText("1001 из 1000")).toBeInTheDocument();
    expect(save).toBeDisabled();
    expect(handlers.onAdd).not.toHaveBeenCalled();
  });
});
