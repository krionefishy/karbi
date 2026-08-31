import { describe, expect, it } from "vitest";

import { belowThreshold, coverLabel, isDormant } from "./cover";
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

  it("shows whole days — a fraction of a day is not a day in hand", () => {
    expect(coverLabel(article(3), 10).text).toBe("3 дн.");
    expect(coverLabel(article(0), 10).text).toBe("0 дн.");
  });

  it("says nothing when the metric could not be computed", () => {
    expect(coverLabel(article(null), 10)).toEqual({ text: "—", tone: "neutral" });
  });

  it("counts only the articles the bot would report", () => {
    const listed = belowThreshold([article(3), article(30), article(null)], 10);

    expect(listed).toHaveLength(1);
    expect(listed[0].days_of_cover).toBe(3);
  });

  it("separates the assortment tail from goods that are actually moving", () => {
    const dead = { ...article(null), stock_total: 0, orders_count: 0 };
    const soldOut = { ...article(null), stock_total: 0, orders_count: 5 };
    const stocked = { ...article(12), stock_total: 40, orders_count: 0 };

    expect(isDormant(dead)).toBe(true);
    expect(isDormant(soldOut)).toBe(false);
    expect(isDormant(stocked)).toBe(false);
  });
});
