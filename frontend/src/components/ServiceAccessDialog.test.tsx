import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Employee } from "../features/admin/types";
import { ServiceAccessDialog } from "./ServiceAccessDialog";

const catalog = [
  { id: "wb-reviews", title: "Мониторинг отзывов" },
  { id: "wb-turnover", title: "Оборачиваемость" },
  { id: "fin-reports", title: "Финансовые отчёты" },
];

const employee: Employee = {
  id: "user-1",
  username: "ivanov",
  is_active: true,
  is_admin: false,
  created_at: "2026-08-22T10:00:00Z",
  last_login_at: null,
  services: ["wb-turnover"],
};

function open(value: Employee, onSubmit = vi.fn()) {
  render(
    <ServiceAccessDialog employee={value} catalog={catalog} pending={false} error="" onClose={() => undefined} onSubmit={onSubmit} />,
  );
  return onSubmit;
}

describe("ServiceAccessDialog", () => {
  it("starts from the granted set and sends the whole new set in catalog order", () => {
    const submit = open(employee);

    expect(screen.getByLabelText("Оборачиваемость")).toBeChecked();
    expect(screen.getByLabelText("Мониторинг отзывов")).not.toBeChecked();

    fireEvent.click(screen.getByLabelText("Финансовые отчёты"));
    fireEvent.click(screen.getByLabelText("Мониторинг отзывов"));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(submit).toHaveBeenCalledWith(["wb-reviews", "wb-turnover", "fin-reports"]);
  });

  it("selects and clears everything in one click", () => {
    const submit = open(employee);

    fireEvent.click(screen.getByRole("button", { name: "Снять все" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(submit).toHaveBeenLastCalledWith([]);

    fireEvent.click(screen.getByRole("button", { name: "Выбрать все" }));
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(submit).toHaveBeenLastCalledWith(["wb-reviews", "wb-turnover", "fin-reports"]);
  });

  it("shows an administrator everything as locked and offers nothing to save", () => {
    open({ ...employee, is_admin: true, services: catalog.map((service) => service.id) });

    expect(screen.getByLabelText("Финансовые отчёты")).toBeChecked();
    expect(screen.getByLabelText("Финансовые отчёты")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Сохранить" })).toBeNull();
  });
});
