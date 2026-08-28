import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ReviewReportDialog } from "./ReviewReportDialog";

describe("ReviewReportDialog", () => {
  it("submits the chosen period", () => {
    const submit = vi.fn();
    render(
      <ReviewReportDialog
        sellerName="ООО Ромашка"
        pending={false}
        error=""
        onClose={() => undefined}
        onSubmit={submit}
      />,
    );
    fireEvent.change(screen.getByLabelText("Начало периода"), { target: { value: "2026-08-01" } });
    fireEvent.change(screen.getByLabelText("Конец периода"), { target: { value: "2026-08-28" } });
    fireEvent.click(screen.getByRole("button", { name: "Скачать XLSX" }));
    expect(submit).toHaveBeenCalledWith("2026-08-01", "2026-08-28");
  });

  it("blocks a period that starts after it ends", () => {
    const submit = vi.fn();
    render(
      <ReviewReportDialog
        sellerName="ООО Ромашка"
        pending={false}
        error=""
        onClose={() => undefined}
        onSubmit={submit}
      />,
    );
    fireEvent.change(screen.getByLabelText("Начало периода"), { target: { value: "2026-08-28" } });
    fireEvent.change(screen.getByLabelText("Конец периода"), { target: { value: "2026-08-01" } });
    expect(screen.getByRole("alert")).toHaveTextContent("Начало периода позже его конца");
    expect(screen.getByRole("button", { name: "Скачать XLSX" })).toBeDisabled();
    expect(submit).not.toHaveBeenCalled();
  });
});
