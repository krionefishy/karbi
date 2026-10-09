import { describe, expect, it } from "vitest";

import { articles, defaultPeriod, localToday, money, uploadSummary } from "./format";
import type { PnlPeriod } from "./types";

const period = (key: string, pending: string[], open = false): PnlPeriod => ({
  key,
  label: key,
  date_from: "2026-09-21",
  date_to: "2026-09-27",
  values: {},
  by_seller: {},
  pending_sellers: pending,
  open,
  uncosted: 0,
});

describe("финансовые отчёты: формат", () => {
  it("показывает ноль прочерком, а деньги — с копейками", () => {
    expect(money(0)).toBe("—");
    expect(money(undefined)).toBe("—");
    expect(money(-0.001)).toBe("—");
    expect(money(-3655954.26).replace(/\s/g, " ")).toBe("-3 655 954,26");
  });

  it("по умолчанию берёт свежий период, где дочитаны все кабинеты", () => {
    expect(defaultPeriod([period("2026-W40", ["a"]), period("2026-W39", []), period("2026-W38", [])])).toBe("2026-W39");
    expect(defaultPeriod([period("2026-W40", ["a"])])).toBe("2026-W40");
    expect(defaultPeriod([])).toBe("");
  });

  it("идущий период по умолчанию не берёт: неделя Ozon по вчерашний день дочитана, но не закрыта", () => {
    expect(defaultPeriod([period("2026-W41", [], true), period("2026-W40", [])])).toBe("2026-W40");
    expect(defaultPeriod([period("2026-W41", [], true)])).toBe("2026-W41");
  });

  it("сегодняшняя дата — по местным часам, а не по UTC", () => {
    expect(localToday(new Date(2026, 9, 9, 1, 30))).toBe("2026-10-09");
  });

  it("склоняет артикулы и пересказывает загрузку файла", () => {
    expect([articles(1), articles(3), articles(11), articles(25)]).toEqual([
      "1 артикул",
      "3 артикула",
      "11 артикулов",
      "25 артикулов",
    ]);
    expect(
      uploadSummary({
        marketplace: "wb",
        added: 2,
        changed: 1,
        unchanged: 86,
        unknown_cabinets: [],
        problems: [],
        effective_from: "2026-10-01",
      }),
    ).toBe("Wildberries: новых — 2, цена изменилась — 1, без изменений — 86.");
  });
});
