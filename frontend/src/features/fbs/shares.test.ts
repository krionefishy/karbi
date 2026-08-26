import { describe, expect, it } from "vitest";

import { BASIS_POINTS, fromPercent, sharesAcceptable, sharesTotal, toPercent } from "./shares";

const shares = (...values: number[]) => values.map((share_bp) => ({ share_bp }));

describe("shares", () => {
  it("converts between percent and the stored hundredths", () => {
    expect(toPercent(4000)).toBe(40);
    expect(fromPercent(12.5)).toBe(1250);
  });

  it("rounds a percent that cannot be stored exactly", () => {
    expect(fromPercent(0.005)).toBe(1);
  });

  it("accepts untouched shares so the logist can still be waiting", () => {
    expect(sharesAcceptable(shares(0, 0, 0, 0, 0, 0))).toBe(true);
  });

  it("accepts a whole hundred percent", () => {
    expect(sharesTotal(shares(4000, 2000, 1200, 1300, 1000, 500))).toBe(BASIS_POINTS);
    expect(sharesAcceptable(shares(4000, 2000, 1200, 1300, 1000, 500))).toBe(true);
  });

  it("rejects a part of a hundred, which would hand out less stock than exists", () => {
    expect(sharesAcceptable(shares(4000, 2000))).toBe(false);
    expect(sharesAcceptable(shares(4000, 2000, 1200, 1300, 1000, 600))).toBe(false);
  });
});
