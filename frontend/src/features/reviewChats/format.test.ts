import { describe, expect, it } from "vitest";

import { defaultPeriod, percent, rateShift, share } from "./format";
import type { GroupSummary } from "./types";

const summary = (replied: number, silent: number, pending = 0): GroupSummary => ({
  total: replied + silent + pending,
  replied,
  silent,
  pending,
  reply_rate: replied + silent ? replied / (replied + silent) : null,
  silent_rate: replied + silent ? silent / (replied + silent) : null,
});

describe("reviewChats format", () => {
  it("период по умолчанию — две недели включая сегодня", () => {
    expect(defaultPeriod(new Date(2026, 8, 18))).toEqual({ dateFrom: "2026-09-05", dateTo: "2026-09-18" });
  });

  it("процент без исходов — прочерк, а не ноль", () => {
    expect(percent(null)).toBe("—");
    expect(percent(0.163)).toMatch(/^16,3\s?%$/);
  });

  it("доля считается от диалогов с известным исходом", () => {
    expect(share(8, summary(8, 32, 10))).toBe("8 из 40");
    expect(share(0, summary(0, 0, 3))).toBe("—");
  });

  it("сдвиг — в процентных пунктах и только когда обе группы не пусты", () => {
    expect(rateShift(summary(10, 90), summary(40, 60))).toBe(-30);
    expect(rateShift(summary(1, 1), summary(0, 0, 2))).toBeNull();
  });
});
