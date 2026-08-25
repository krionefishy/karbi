import { describe, expect, it } from "vitest";

import { buildSeries, formatSigned, runsOf, summarize, ticksFor, verticalScale } from "./chart";
import type { DailyReviewSnapshot } from "./types";

const day = (date: string, total: number): DailyReviewSnapshot => ({
  date,
  ratings: { 1: 0, 2: 0, 3: 0, 4: 0, 5: total },
});

describe("buildSeries", () => {
  it("covers exactly the requested window, ending on the given day", () => {
    const points = buildSeries([], "2026-08-25", 30);
    expect(points).toHaveLength(30);
    expect(points[0].date).toBe("2026-07-27");
    expect(points[29].date).toBe("2026-08-25");
  });

  it("leaves an uncollected day empty instead of calling it zero", () => {
    const points = buildSeries([day("2026-08-24", 900)], "2026-08-25", 2);
    expect(points.map((point) => point.total)).toEqual([900, null]);
  });

  it("has no delta on a day whose predecessor is missing", () => {
    // 23rd was never collected, so the 24th's change is unknown — not a jump.
    const points = buildSeries([day("2026-08-22", 900), day("2026-08-24", 903)], "2026-08-25", 4);
    expect(points.map((point) => point.delta)).toEqual([null, null, null, null]);
  });

  it("reports the change against the day before", () => {
    const points = buildSeries([day("2026-08-24", 900), day("2026-08-25", 903)], "2026-08-25", 2);
    expect(points[1].delta).toBe(3);
  });

  it("reports reviews disappearing as a negative change", () => {
    const points = buildSeries([day("2026-08-24", 903), day("2026-08-25", 890)], "2026-08-25", 2);
    expect(points[1].delta).toBe(-13);
  });

  it("ignores snapshots from outside the window", () => {
    const points = buildSeries([day("2026-01-01", 5), day("2026-08-25", 900)], "2026-08-25", 3);
    expect(points.map((point) => point.total)).toEqual([null, null, 900]);
  });
});

describe("verticalScale", () => {
  it("frames the data instead of starting at zero", () => {
    const points = buildSeries([day("2026-08-24", 1289), day("2026-08-25", 1336)], "2026-08-25", 2);
    const scale = verticalScale(points);
    expect(scale.floor).toBeGreaterThan(1200);
    expect(scale.ceiling).toBeLessThan(1400);
    expect(scale.floor).toBeLessThan(1289);
    expect(scale.ceiling).toBeGreaterThan(1336);
  });

  it("keeps a height for a product that never moved", () => {
    const points = buildSeries([day("2026-08-24", 500), day("2026-08-25", 500)], "2026-08-25", 2);
    const scale = verticalScale(points);
    expect(scale.ceiling).toBeGreaterThan(scale.floor);
  });

  it("survives a window with nothing collected in it", () => {
    const scale = verticalScale(buildSeries([], "2026-08-25", 5));
    expect(scale.ceiling).toBeGreaterThan(scale.floor);
  });
});

describe("ticksFor", () => {
  it("labels the axis in whole reviews without repeating a value", () => {
    const ticks = ticksFor({ floor: 1280, ceiling: 1340 });
    expect(ticks).toEqual([1280, 1300, 1320, 1340]);
  });

  it("collapses duplicates on a very short range", () => {
    expect(new Set(ticksFor({ floor: 10, ceiling: 11 })).size).toBe(ticksFor({ floor: 10, ceiling: 11 }).length);
  });
});

describe("runsOf", () => {
  it("breaks the line where days are missing", () => {
    const points = buildSeries(
      [day("2026-08-21", 10), day("2026-08-22", 11), day("2026-08-25", 14)],
      "2026-08-25",
      5,
    );
    const runs = runsOf(points);
    expect(runs).toHaveLength(2);
    expect(runs[0].map((point) => point.total)).toEqual([10, 11]);
    expect(runs[1].map((point) => point.total)).toEqual([14]);
  });

  it("keeps the position of each point so the gap has a width", () => {
    const points = buildSeries([day("2026-08-21", 10), day("2026-08-25", 14)], "2026-08-25", 5);
    const runs = runsOf(points);
    expect([runs[0][0].index, runs[1][0].index]).toEqual([0, 4]);
  });

  it("has nothing to draw when the window is empty", () => {
    expect(runsOf(buildSeries([], "2026-08-25", 5))).toEqual([]);
  });
});

describe("summarize", () => {
  it("counts the month's change, the days that moved and the days we lost", () => {
    const points = buildSeries(
      [day("2026-08-21", 900), day("2026-08-22", 903), day("2026-08-25", 910)],
      "2026-08-25",
      5,
    );
    expect(summarize(points)).toEqual({ latestTotal: 910, change: 10, movedDays: 1, missingDays: 2 });
  });

  it("has nothing to report for a product with no history yet", () => {
    expect(summarize(buildSeries([], "2026-08-25", 5))).toEqual({
      latestTotal: null,
      change: null,
      movedDays: 0,
      missingDays: 5,
    });
  });
});

describe("formatSigned", () => {
  it("marks direction with a real minus sign", () => {
    expect(formatSigned(3)).toBe("+3");
    expect(formatSigned(-13)).toBe("−13");
    expect(formatSigned(0)).toBe("0");
    // ru-RU groups digits with a non-breaking space, so normalise before comparing.
    expect(formatSigned(13804).replace(/\s/g, " ")).toBe("+13 804");
  });
});
