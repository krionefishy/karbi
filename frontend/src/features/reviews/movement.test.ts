import { describe, expect, it } from "vitest";

import { latestCollectedDate, movementOn, sortByMovement } from "./movement";
import type { RatingCounts } from "./types";

const totalling = (total: number): RatingCounts => ({ 1: 0, 2: 0, 3: 0, 4: 0, 5: total });

const product = (id: string, days: Record<string, number>) => ({
  id,
  snapshots: Object.entries(days).map(([date, total]) => ({ date, ratings: totalling(total) })),
});

describe("latestCollectedDate", () => {
  it("is the newest day present anywhere in the data", () => {
    const products = [
      product("a", { "2026-08-17": 10, "2026-08-18": 12 }),
      product("b", { "2026-08-16": 5, "2026-08-19": 7 }),
    ];
    expect(latestCollectedDate(products)).toBe("2026-08-19");
  });

  it("is null when nothing has been collected", () => {
    expect(latestCollectedDate([])).toBeNull();
    expect(latestCollectedDate([product("a", {})])).toBeNull();
  });
});

describe("movementOn", () => {
  it("reports the change in the total review count", () => {
    const item = product("a", { "2026-08-17": 900, "2026-08-18": 903 });
    expect(movementOn(item, "2026-08-18")).toEqual({ delta: 3, latest: 903, previous: 900 });
  });

  it("reports reviews disappearing as a negative change", () => {
    const item = product("a", { "2026-08-17": 903, "2026-08-18": 890 });
    expect(movementOn(item, "2026-08-18").delta).toBe(-13);
  });

  it("has no delta when the day before is missing", () => {
    const item = product("a", { "2026-08-16": 900, "2026-08-18": 903 });
    expect(movementOn(item, "2026-08-18")).toEqual({ delta: null, latest: 903, previous: null });
  });

  it("has nothing to report when the product was not collected that day", () => {
    const item = product("a", { "2026-08-17": 900 });
    expect(movementOn(item, "2026-08-18").delta).toBeNull();
    expect(movementOn(item, null).delta).toBeNull();
  });
});

describe("sortByMovement", () => {
  const day = "2026-08-18";

  it("lifts the products that moved, biggest change first", () => {
    const products = [
      product("still", { "2026-08-17": 500, [day]: 500 }),
      product("gained-three", { "2026-08-17": 900, [day]: 903 }),
      product("lost-thirteen", { "2026-08-17": 903, [day]: 890 }),
    ];

    expect(sortByMovement(products, day).map((entry) => entry.id)).toEqual([
      "lost-thirteen",
      "gained-three",
      "still",
    ]);
  });

  it("treats a loss and a gain of the same size as equally worth seeing", () => {
    const products = [
      product("lost", { "2026-08-17": 100, [day]: 95 }),
      product("gained", { "2026-08-17": 100, [day]: 105 }),
    ];
    // Equal magnitude, so the incoming order decides and neither jumps ahead.
    expect(sortByMovement(products, day).map((entry) => entry.id)).toEqual(["lost", "gained"]);
  });

  it("keeps the incoming order among everything that did not move", () => {
    const products = [
      product("first", { "2026-08-17": 10, [day]: 10 }),
      product("second", {}),
      product("third", { [day]: 4 }),
    ];
    expect(sortByMovement(products, day).map((entry) => entry.id)).toEqual(["first", "second", "third"]);
  });

  it("leaves the list untouched when nothing has been collected", () => {
    const products = [product("a", {}), product("b", {})];
    expect(sortByMovement(products, null).map((entry) => entry.id)).toEqual(["a", "b"]);
  });

  it("does not mutate the array it was given", () => {
    const products = [
      product("still", { "2026-08-17": 5, [day]: 5 }),
      product("moved", { "2026-08-17": 5, [day]: 9 }),
    ];
    sortByMovement(products, day);
    expect(products.map((entry) => entry.id)).toEqual(["still", "moved"]);
  });
});
