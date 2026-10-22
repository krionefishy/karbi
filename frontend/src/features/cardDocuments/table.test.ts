import { describe, expect, it } from "vitest";

import {
  agoText,
  banners,
  daysLeftText,
  daysTone,
  documentPeriod,
  emptyMessage,
  reasonLabel,
  stateTone,
  tiles,
  visibleItems,
} from "./table";
import type { CardDocumentItem, CardDocuments, CardDocumentsSummary, Notice } from "./types";

const summary: CardDocumentsSummary = { total: 5, ok: 2, rejected: 1, missing: 0, expiring: 1, mismatch: 1 };

function item(nmId: number, overrides: Partial<CardDocumentItem> = {}): CardDocumentItem {
  return {
    nm_id: nmId,
    vendor_code: `SKU-${nmId}`,
    title: `Товар ${nmId}`,
    subject_name: "Воздуходувки",
    state: "ok",
    reason: "valid",
    document_number: `ЕАЭС N RU Д-${nmId}`,
    document_start: "2025-01-10",
    document_end: "2027-01-09",
    days_left: null,
    checked_at: null,
    since: null,
    ...overrides,
  };
}

function data(overrides: Partial<CardDocuments> = {}): CardDocuments {
  return { collected_at: "2026-10-22T07:00:00Z", last_error: null, stale: false, summary, items: [], notices: [], ...overrides };
}

const now = new Date("2026-10-22T19:00:00Z");

function notice(kind: string, overrides: Partial<Notice> = {}): Notice {
  return { kind, level: "warning", since: null, detail: null, ...overrides };
}

function only(item: Notice, canInvite = true) {
  const [first, ...rest] = banners(data({ notices: [item] }), canInvite, now);
  expect(rest).toEqual([]);
  return first;
}

describe("card documents table", () => {
  it("names WB reasons in Russian and keeps unknown ones as they came", () => {
    expect(reasonLabel("document_not_found")).toBe("Документ не найден в реестре");
    expect(reasonLabel("documents_missing")).toBe("Документ не загружен");
    expect(reasonLabel("documents_invalid")).toBe("Документ признан недействительным");
    expect(reasonLabel("valid")).toBe("Документ действителен");
    expect(reasonLabel("expired")).toBe("Срок действия истёк");
    expect(reasonLabel("number_mismatch")).toBe("Номер в характеристиках не совпадает с документом");
    expect(reasonLabel("start_date_mismatch")).toContain("Дата регистрации");
    expect(reasonLabel("end_date_mismatch")).toContain("Дата окончания");
    expect(reasonLabel("status_2")).toContain("причину не указал");
    expect(reasonLabel("wrong_period")).toBe("wrong_period");
    expect(reasonLabel(null)).toBe("—");
  });

  it("colours states and the days left", () => {
    expect(stateTone("rejected")).toBe("negative");
    expect(stateTone("expiring")).toBe("warning");
    expect(stateTone("ok")).toBe("positive");
    expect(daysTone(5)).toBe("negative");
    expect(daysTone(14)).toBe("warning");
    expect(daysTone(90)).toBe("neutral");
    expect(daysTone(null)).toBe("neutral");
  });

  it("says how long the document lives", () => {
    expect(daysLeftText(12)).toBe("осталось 12 дн.");
    expect(daysLeftText(0)).toBe("истекает сегодня");
    expect(daysLeftText(-3)).toBe("истёк 3 дн. назад");
    expect(daysLeftText(null)).toBe("—");
    expect(documentPeriod("2025-01-10", "2027-01-09")).toBe("10.01.2025 – 09.01.2027");
    expect(documentPeriod("2025-01-10", null)).toBe("с 10.01.2025, бессрочно");
    expect(documentPeriod(null, null)).toBe("");
  });

  it("counts problems for the first tile", () => {
    const counts = Object.fromEntries(tiles(summary).map((tile) => [tile.key, tile.count]));
    expect(counts).toMatchObject({ problems: 3, rejected: 1, ok: 2, all: 5 });
  });

  it("filters by tile and search keeping the backend order", () => {
    const items = [
      item(3, { state: "rejected", reason: "document_not_found" }),
      item(1, { state: "expiring", days_left: 7 }),
      item(2),
    ];

    expect(visibleItems(items, "problems", "").map((row) => row.nm_id)).toEqual([3, 1]);
    expect(visibleItems(items, "all", "").map((row) => row.nm_id)).toEqual([3, 1, 2]);
    expect(visibleItems(items, "ok", "").map((row) => row.nm_id)).toEqual([2]);
    expect(visibleItems(items, "all", "sku-1").map((row) => row.nm_id)).toEqual([1]);
    expect(visibleItems(items, "all", "Д-3").map((row) => row.nm_id)).toEqual([3]);
  });

  it("shows no banners when the backend reports nothing, even with legacy fields set", () => {
    expect(banners(data({ stale: true, last_error: "WB ответил 401" }), true, now)).toEqual([]);
  });

  it("tells how long ago the last collection was for stale", () => {
    const stale = only(notice("stale", { since: "2026-10-22T07:00:00Z" }));
    expect(stale.title).toBe("Наблюдение не работает");
    expect(stale.text).toContain("Последний сбор был 12 ч назад");
    expect(stale.text).toContain("10:00 МСК");
    expect(stale.text).toContain("Состояния ниже могли устареть");
    expect(only(notice("stale"), true).text).toContain("12 ч назад");
    expect(banners(data({ collected_at: null, notices: [notice("stale")] }), true, now)[0].text).toContain(
      "ещё не было",
    );
  });

  it("renders collection and permanent errors with the detail", () => {
    const failed = only(notice("collection_error", { level: "danger", detail: "WB ответил 429" }));
    expect(failed).toMatchObject({
      level: "danger",
      title: "Последний сбор не удался, показаны прежние состояния",
      text: "WB ответил 429",
    });
    const permanent = only(notice("permanent_error", { level: "danger", detail: "ключ без права на «Контент»" }));
    expect(permanent.title).toBe("Кабинет не читается");
    expect(permanent.text).toBe("ключ без права на «Контент». Нужно вмешательство.");
  });

  it("asks to share the bot link when nobody is subscribed", () => {
    const withButton = only(notice("no_subscribers"));
    expect(withButton.title).toBe("Некому слать уведомления: на кабинет никто не подписан");
    expect(withButton.text).toContain("«Ссылка на бота»");
    expect(only(notice("no_subscribers"), false).text).toBeNull();
  });

  it("sends to the administrator when the bot is missing", () => {
    expect(only(notice("bot_missing", { level: "danger" }))).toMatchObject({
      level: "danger",
      title: "Бот уведомлений не зарегистрирован — обратитесь к администратору",
    });
  });

  it("shows an unknown kind as it came with its level", () => {
    expect(only(notice("quota_low", { level: "danger", detail: "Квота почти кончилась" }))).toMatchObject({
      level: "danger",
      title: "Квота почти кончилась",
    });
    expect(only(notice("quota_low")).title).toBe("quota_low");
  });

  it("counts time ago in minutes, hours and days", () => {
    expect(agoText("2026-10-22T18:35:00Z", now)).toBe("25 мин назад");
    expect(agoText("2026-10-19T19:00:00Z", now)).toBe("3 дн. назад");
  });

  it("explains an empty table", () => {
    expect(emptyMessage("problems", summary, "")).toContain("у всех 5 карточек");
    expect(emptyMessage("problems", { ...summary, total: 0 }, "")).toContain("нет карточек с документами");
    expect(emptyMessage("rejected", summary, "zzz")).toBe("По запросу карточек не нашлось.");
  });
});
