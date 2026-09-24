import { describe, expect, it } from "vitest";

import { coverLabel, sellerProblem, shortDay } from "./format";
import type { PodsortSellerState } from "./types";

const healthy: PodsortSellerState = {
  seller_id: "1",
  name: "Байбурин",
  window_days_loaded: 7,
  history_from: "2026-06-24",
  history_days_loaded: 92,
  collected_at: "2026-09-24T06:00:00Z",
  collection_error: null,
  remains_at: "2026-09-24T06:00:00Z",
  remains_error: null,
};

describe("подсорт: подписи", () => {
  it("день API не съезжает на часовой пояс", () => {
    expect(shortDay("2026-09-17")).toBe("17.09");
  });

  it("говорит о кабинете только то, что мешает верить цифрам", () => {
    expect(sellerProblem(healthy, 7)).toBeNull();
    expect(sellerProblem({ ...healthy, window_days_loaded: 3 }, 7)).toContain("3 из 7");
    expect(sellerProblem({ ...healthy, remains_at: null }, 7)).toContain("остаток принят за 0");
    expect(sellerProblem({ ...healthy, collection_error: "HTTP 429" }, 7)).toContain("HTTP 429");
  });

  it("дни покрытия без продаж — прочерк, огромные — 100+", () => {
    expect(coverLabel(null)).toBe("—");
    expect(coverLabel(250)).toBe("100+");
    expect(coverLabel(9.52)).toBe("9,5");
  });
});
