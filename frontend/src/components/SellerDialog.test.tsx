import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SellerDialog } from "./SellerDialog";

describe("SellerDialog", () => {
  it("submits seller name and API key", () => {
    const submit = vi.fn();
    render(<SellerDialog pending={false} error="" onClose={() => undefined} onSubmit={submit} />);
    fireEvent.change(screen.getByLabelText("Название селлера"), { target: { value: "ООО Ромашка" } });
    fireEvent.change(screen.getByLabelText("WB Seller API Key"), { target: { value: "wb-api-key-123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить селлера" }));
    expect(submit).toHaveBeenCalledWith({ name: "ООО Ромашка", api_key: "wb-api-key-123456" });
  });
});
