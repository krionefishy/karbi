import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { EmployeeDialog, IssuedPasswordDialog } from "./EmployeeDialog";

const issued = {
  password: "Xk7fQr2mTb9vHs4dLp6w",
  user: {
    id: "user-1",
    username: "ivanov",
    is_active: true,
    is_admin: false,
    created_at: "2026-08-22T10:00:00Z",
    last_login_at: null,
  },
};

describe("EmployeeDialog", () => {
  it("submits a login and the admin flag, and asks for no password", () => {
    const submit = vi.fn();
    render(<EmployeeDialog pending={false} error="" onClose={() => undefined} onSubmit={submit} />);

    fireEvent.change(screen.getByLabelText("Логин"), { target: { value: "  ivanov  " } });
    fireEvent.click(screen.getByLabelText("Доступ к админке"));
    fireEvent.click(screen.getByRole("button", { name: "Добавить" }));

    expect(submit).toHaveBeenCalledWith({ username: "ivanov", is_admin: true });
    expect(screen.queryByLabelText(/пароль/i)).toBeNull();
  });
});

describe("IssuedPasswordDialog", () => {
  it("shows the generated password and says it will not come back", () => {
    render(<IssuedPasswordDialog issued={issued} onClose={() => undefined} />);

    expect(screen.getByText(issued.password)).toBeTruthy();
    expect(screen.getByText(/Показывается один раз/)).toBeTruthy();
  });
});
