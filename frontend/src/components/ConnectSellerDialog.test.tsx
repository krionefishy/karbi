import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Seller } from "../features/sellers/types";
import { ConnectSellerDialog } from "./ConnectSellerDialog";

function seller(id: string, name: string): Seller {
  return {
    id,
    name,
    product_count: 12,
    catalog_sync_status: "success",
    last_catalog_sync_at: null,
    catalog_sync_error: null,
    archived_at: null,
    automations: [],
  };
}

function renderDialog(available: Seller[], onConnect = vi.fn()) {
  render(
    <ConnectSellerDialog
      automationTitle="Мониторинг отзывов Wildberries"
      available={available}
      pending={false}
      error=""
      onClose={() => undefined}
      onConnect={onConnect}
    />,
  );
  return onConnect;
}

describe("ConnectSellerDialog", () => {
  it("connects a seller that already exists in the registry", () => {
    const onConnect = renderDialog([seller("seller-1", "ООО Ромашка"), seller("seller-2", "ООО Василёк")]);

    fireEvent.change(screen.getByLabelText("Селлер"), { target: { value: "seller-2" } });
    fireEvent.click(screen.getByRole("button", { name: "Подключить" }));

    expect(onConnect).toHaveBeenCalledWith({ seller_id: "seller-2" });
  });

  it("creates a new seller when asked to", () => {
    const onConnect = renderDialog([seller("seller-1", "ООО Ромашка")]);

    fireEvent.click(screen.getByRole("tab", { name: "Новый селлер" }));
    fireEvent.change(screen.getByLabelText("Название селлера"), { target: { value: "ООО Новый" } });
    fireEvent.change(screen.getByLabelText("WB Seller API Key"), { target: { value: "wb-api-key-123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Подключить" }));

    expect(onConnect).toHaveBeenCalledWith({ name: "ООО Новый", api_key: "wb-api-key-123456" });
  });

  it("falls back to creating when the registry has nobody left to connect", () => {
    renderDialog([]);

    expect(screen.getByRole("tab", { name: "Из реестра" })).toBeDisabled();
    expect(screen.getByLabelText("Название селлера")).toBeInTheDocument();
  });
});
