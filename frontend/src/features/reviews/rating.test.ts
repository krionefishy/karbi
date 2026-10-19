import { describe, expect, it } from "vitest";

import { averageRating, fiveStarsToTarget, pluralizeFives, ratingDebt, totalReviews } from "./rating";
import type { RatingCounts } from "./types";

const counts = (one: number, two: number, three: number, four: number, five: number): RatingCounts => ({
  1: one,
  2: two,
  3: three,
  4: four,
  5: five,
});

describe("ratingDebt", () => {
  it("weighs a review by how far it sits from five", () => {
    expect(ratingDebt(counts(1, 0, 0, 0, 0))).toBe(4);
    expect(ratingDebt(counts(0, 0, 0, 1, 0))).toBe(1);
    expect(ratingDebt(counts(0, 0, 0, 0, 999))).toBe(0);
    expect(ratingDebt(counts(100, 38, 60, 123, 1015))).toBe(757);
  });
});

describe("fiveStarsToTarget", () => {
  it("matches the closed form 20·debt − total at the 4.95 threshold", () => {
    expect(fiveStarsToTarget(counts(100, 38, 60, 123, 1015))).toBe(13804);
    expect(fiveStarsToTarget(counts(18, 10, 8, 102, 801))).toBe(3461);
  });

  it("does not round up a value that is already exact", () => {
    expect(fiveStarsToTarget(counts(0, 0, 0, 1, 0))).toBe(19);
    expect(fiveStarsToTarget(counts(1, 0, 0, 0, 0))).toBe(79);
    expect(fiveStarsToTarget(counts(0, 0, 0, 20, 380))).toBe(0);
    expect(fiveStarsToTarget(counts(0, 0, 1, 0, 39))).toBe(0);
  });

  it("asks for nothing once the threshold is met", () => {
    expect(fiveStarsToTarget(counts(0, 0, 0, 0, 10))).toBe(0);
    expect(fiveStarsToTarget(counts(0, 0, 0, 1, 100))).toBe(0);
  });

  it("treats an empty distribution as nothing to chase", () => {
    expect(fiveStarsToTarget(counts(0, 0, 0, 0, 0))).toBe(0);
    expect(averageRating(counts(0, 0, 0, 0, 0))).toBeNull();
    expect(totalReviews(counts(0, 0, 0, 0, 0))).toBe(0);
  });

  it("crosses the boundary exactly at 4.95", () => {
    expect(averageRating(counts(0, 0, 0, 1, 19))).toBeCloseTo(4.95, 10);
    expect(fiveStarsToTarget(counts(0, 0, 0, 1, 19))).toBe(0);
    expect(fiveStarsToTarget(counts(0, 0, 0, 1, 18))).toBe(1);
  });

  it("respects a caller-supplied threshold", () => {
    expect(fiveStarsToTarget(counts(0, 0, 0, 1, 0), 4.5)).toBe(1);
    expect(fiveStarsToTarget(counts(0, 0, 0, 1, 0), 4.95)).toBe(19);
  });
});

describe("averageRating", () => {
  it("is the plain arithmetic mean of the distribution", () => {
    expect(averageRating(counts(0, 0, 0, 0, 5))).toBe(5);
    expect(averageRating(counts(1, 0, 0, 0, 1))).toBe(3);
    expect(averageRating(counts(100, 38, 60, 123, 1015))).toBeCloseTo(4.433, 3);
  });
});

describe("pluralizeFives", () => {
  it("agrees with the numeral", () => {
    expect(pluralizeFives(1)).toBe("пятёрка");
    expect(pluralizeFives(3461)).toBe("пятёрка");
    expect(pluralizeFives(2)).toBe("пятёрки");
    expect(pluralizeFives(13804)).toBe("пятёрки");
    expect(pluralizeFives(5)).toBe("пятёрок");
    expect(pluralizeFives(11)).toBe("пятёрок");
    expect(pluralizeFives(13805)).toBe("пятёрок");
  });
});
