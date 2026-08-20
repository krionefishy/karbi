import { describe, expect, it } from "vitest";

import { belowThreshold, coverLabel } from "./cover";
import type { ArticleTurnover } from "./types";

function article(days: number | null): ArticleTurnover {
  return {
    article: "101",
    name: "Товар",
    photo_url: "",
    stock_total: 10,
    stock_fbo: 10,
    stock_fbs: 0,
    avg_stock: 10,
    orders_count: 14,
    avg_daily_orders: 1,
    days_of_cover: days,
    turnover_days: days,
    stock_days: 14,
    status: days === null ? "no_sales" : "ok",
  };
}

describe("cover", () => {
  it("separates urgent from merely low", () => {
    expect(coverLabel(article(3), 10).tone).toBe("critical");
    expect(coverLabel(article(8), 10).tone).toBe("warning");
    expect(coverLabel(article(30), 10).tone).toBe("calm");
  });

  it("says nothing when the metric could not be computed", () => {
    expect(coverLabel(article(null), 10)).toEqual({ text: "—", tone: "neutral" });
  });

  it("counts only the articles the bot would report", () => {
    const listed = belowThreshold([article(3), article(30), article(null)], 10);

    expect(listed).toHaveLength(1);
    expect(listed[0].days_of_cover).toBe(3);
  });
});
