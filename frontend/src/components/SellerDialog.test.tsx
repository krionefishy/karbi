import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Seller } from "../features/sellers/types";
import { MPStatsCredentialsDialog, OzonCredentialsDialog, SellerDialog } from "./SellerDialog";

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

function seller(overrides: Partial<Seller> = {}): Seller {
  return {
    id: "seller-1",
    name: "ООО Ромашка",
    product_count: 12,
    catalog_sync_status: "success",
    last_catalog_sync_at: null,
    catalog_sync_error: null,
    archived_at: null,
    automations: [],
    egress_status: "verified",
    egress_error: null,
    ozon_egress_status: "undelivered",
    ozon_egress_error: null,
    mpstats_egress_status: "undelivered",
    mpstats_egress_error: null,
    egress_ip: null,
    ...overrides,
  };
}

function renderOzon(target: Seller, onVerify = vi.fn(), onSubmit = vi.fn()) {
  render(
    <OzonCredentialsDialog
      seller={target}
      pending={false}
      verifying={false}
      error=""
      onClose={() => undefined}
      onSubmit={onSubmit}
      onVerify={onVerify}
    />,
  );
  return { onVerify, onSubmit };
}

describe("OzonCredentialsDialog", () => {
  it("submits the whole account, Performance pair included", () => {
    const { onSubmit } = renderOzon(seller());
    fireEvent.change(screen.getByLabelText("Client-Id"), { target: { value: "111222" } });
    fireEvent.change(screen.getByLabelText("Api-Key (Seller API)"), { target: { value: "ozon-api-key" } });
    fireEvent.change(screen.getByLabelText("Performance client_id — необязательно"), {
      target: { value: "42@advertising.performance.ozon.ru" },
    });
    fireEvent.change(screen.getByLabelText("Performance client_secret"), {
      target: { value: "perf-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Подключить" }));

    expect(onSubmit).toHaveBeenCalledWith({
      client_id: "111222",
      api_key: "ozon-api-key",
      performance_client_id: "42@advertising.performance.ozon.ru",
      performance_client_secret: "perf-secret",
    });
  });

  it("submits an empty Performance pair as empty strings, not as undefined", () => {
    const { onSubmit } = renderOzon(seller());
    fireEvent.change(screen.getByLabelText("Client-Id"), { target: { value: "111222" } });
    fireEvent.change(screen.getByLabelText("Api-Key (Seller API)"), { target: { value: "ozon-api-key" } });
    fireEvent.click(screen.getByRole("button", { name: "Подключить" }));

    expect(onSubmit).toHaveBeenCalledWith({
      client_id: "111222",
      api_key: "ozon-api-key",
      performance_client_id: "",
      performance_client_secret: "",
    });
  });

  it("offers a re-check only once there is an account to re-check", () => {
    renderOzon(seller());
    expect(screen.queryByRole("button", { name: "Перепроверить" })).toBeNull();
  });

  it("re-checks an account that the gateway refused", () => {
    const { onVerify } = renderOzon(
      seller({ ozon_egress_status: "key_invalid", ozon_egress_error: "HTTP 401 от Ozon" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Перепроверить" }));

    expect(onVerify).toHaveBeenCalled();
  });
});

function renderMPStats(target: Seller, onVerify = vi.fn(), onSubmit = vi.fn()) {
  render(
    <MPStatsCredentialsDialog
      seller={target}
      pending={false}
      verifying={false}
      error=""
      onClose={() => undefined}
      onSubmit={onSubmit}
      onVerify={onVerify}
    />,
  );
  return { onVerify, onSubmit };
}

describe("MPStatsCredentialsDialog", () => {
  it("submits the token trimmed of what came along with the paste", () => {
    const { onSubmit } = renderMPStats(seller());
    fireEvent.change(screen.getByLabelText("Токен MPStats"), {
      target: { value: "  aaaa1111.bbbb2222  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Подключить" }));

    expect(onSubmit).toHaveBeenCalledWith({ token: "aaaa1111.bbbb2222" });
  });

  it("keeps the token hidden until asked to show it", () => {
    renderMPStats(seller());
    const field = screen.getByLabelText("Токен MPStats");
    expect(field.getAttribute("type")).toBe("password");

    fireEvent.click(screen.getByRole("button", { name: "Показать токен" }));

    expect(field.getAttribute("type")).toBe("text");
  });

  it("offers a re-check only once there is a token to re-check", () => {
    renderMPStats(seller());
    expect(screen.queryByRole("button", { name: "Перепроверить" })).toBeNull();
  });

  it("re-checks a token that the gateway could not verify", () => {
    const { onVerify } = renderMPStats(
      seller({ mpstats_egress_status: "delivered", mpstats_egress_error: "Проверка не состоялась" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Перепроверить" }));

    expect(onVerify).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Заменить токен" })).toBeTruthy();
  });
});
