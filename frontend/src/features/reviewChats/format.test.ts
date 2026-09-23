import { describe, expect, it } from "vitest";

import { defaultPeriod, percent, rateShift, share, shiftLabel } from "./format";
import type { GroupSummary } from "./types";

const summary = (replied: number, silent: number, pending = 0): GroupSummary => {
  const total = replied + silent + pending;
  return {
    total,
    replied,
    silent,
    pending,
    reply_rate: total ? replied / total : null,
    silent_rate: total ? silent / total : null,
    pending_rate: total ? pending / total : null,
  };
};

describe("reviewChats format", () => {
  it("период по умолчанию — две недели включая сегодня", () => {
    expect(defaultPeriod(new Date(2026, 8, 18))).toEqual({ dateFrom: "2026-09-05", dateTo: "2026-09-18" });
  });

  it("процент без диалогов — прочерк, а не ноль", () => {
    expect(percent(null)).toBe("—");
    expect(percent(0.163)).toMatch(/^16,3\s?%$/);
  });

  it("доля считается от всех диалогов, ожидающие — в знаменателе", () => {
    expect(share(9, summary(9, 0, 10))).toBe("9 из 19");
    expect(share(0, summary(0, 0))).toBe("—");
  });

  it("сдвиг — в процентных пунктах и только когда обе группы не пусты", () => {
    expect(rateShift(summary(10, 90), summary(40, 60))).toBe(-30);
    expect(rateShift(summary(1, 1), summary(0, 0))).toBeNull();
    expect(shiftLabel(-30)).toBe("-30 п.п.");
    expect(shiftLabel(2.5)).toBe("+2,5 п.п.");
    expect(shiftLabel(null)).toBe("—");
  });
});
