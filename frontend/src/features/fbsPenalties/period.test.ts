import { describe, expect, it } from "vitest";

import { defaultPeriod, money, periodLabel } from "./period";

describe("период штрафов", () => {
  it("по умолчанию — последняя неделя", () => {
    expect(defaultPeriod(new Date(2026, 8, 16))).toEqual({ dateFrom: "2026-09-09", dateTo: "2026-09-16" });
  });

  it("неделя отчёта читается по-русски", () => {
    expect(periodLabel("2026-09-01/2026-09-07")).toBe("01.09 — 07.09.2026");
    expect(periodLabel("странно")).toBe("странно");
  });

  it("сумма — с копейками и рублём", () => {
    expect(money(198.06).replace(/ /g, " ")).toBe("198,06 ₽");
  });
});
