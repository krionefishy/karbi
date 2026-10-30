import { describe, expect, it } from "vitest";

import {
  customRangeError,
  formatBytes,
  formatCountdown,
  formatMoment,
  hasPending,
  moscowToday,
  outcomeLabel,
  periodRange,
  plural,
  statusBadgeClass,
  statusLabel,
  submissionNote,
  zoneLabel,
} from "./format";
import type { Submission } from "./types";

function submission(overrides: Partial<Submission> = {}): Submission {
  return {
    id: "s-1",
    channel: "web",
    received_at: "2026-10-23T11:32:00Z",
    outcome: "positive",
    status: "accepted",
    reason_code: null,
    reason_text: null,
    duplicate_received_at: null,
    fields: { review_at: null, stars: 5, text_preview: null, chat_tail: "a1b2c3" },
    thumb_url: null,
    ...overrides,
  };
}

describe("screenshot control format", () => {
  it("takes today's date in Moscow, not in UTC", () => {
    expect(moscowToday(new Date("2026-10-22T21:30:00Z"))).toBe("2026-10-23");
    expect(moscowToday(new Date("2026-10-22T20:30:00Z"))).toBe("2026-10-22");
  });

  it("builds day, week from Monday and month from the first", () => {
    const friday = new Date("2026-10-23T09:00:00Z");
    expect(periodRange("day", friday)).toEqual({ from: "2026-10-23", to: "2026-10-23" });
    expect(periodRange("week", friday)).toEqual({ from: "2026-10-19", to: "2026-10-23" });
    expect(periodRange("month", friday)).toEqual({ from: "2026-10-01", to: "2026-10-23" });
    const sunday = new Date("2026-10-25T09:00:00Z");
    expect(periodRange("week", sunday)).toEqual({ from: "2026-10-19", to: "2026-10-25" });
    const monday = new Date("2026-11-02T09:00:00Z");
    expect(periodRange("week", monday)).toEqual({ from: "2026-11-02", to: "2026-11-02" });
  });

  it("checks a custom range", () => {
    expect(customRangeError({ from: "2026-10-01", to: "2026-10-23" })).toBe("");
    expect(customRangeError({ from: "2026-10-01", to: "2026-10-01" })).toBe("");
    expect(customRangeError({ from: "2026-10-24", to: "2026-10-23" })).toBe("Начало позже конца");
    expect(customRangeError({ from: "", to: "2026-10-23" })).toBe("Укажите обе даты");
  });

  it("declines Russian nouns by number", () => {
    const forms: [string, string, string] = ["файл", "файла", "файлов"];
    expect([0, 1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 101, 111].map((n) => plural(n, forms))).toEqual([
      "файлов",
      "файл",
      "файла",
      "файла",
      "файлов",
      "файлов",
      "файлов",
      "файлов",
      "файл",
      "файла",
      "файлов",
      "файл",
      "файлов",
    ]);
  });

  it("names outcomes and statuses the way managers say them", () => {
    expect(outcomeLabel("positive")).toBe("Полож");
    expect(outcomeLabel("amended")).toBe("Доп");
    expect(outcomeLabel("deleted")).toBe("Удален");
    expect(outcomeLabel(null)).toBe("без исхода");
    expect(statusLabel("processing")).toBe("В обработке");
    expect(statusLabel("accepted")).toBe("Принят");
    expect(statusLabel("needs_review")).toBe("Проверяется");
    expect(statusLabel("duplicate")).toBe("Дубль");
    expect(statusLabel("rejected")).toBe("Не засчитан");
  });

  it("colours badges by status", () => {
    expect(statusBadgeClass("processing")).toBe("status-badge status-running");
    expect(statusBadgeClass("accepted")).toBe("status-badge status-active");
    expect(statusBadgeClass("needs_review")).toBe("status-badge status-degraded");
    expect(statusBadgeClass("duplicate")).toBe("status-badge status-failed");
    expect(statusBadgeClass("rejected")).toBe("status-badge");
  });

  it("polls only while the pipeline is still processing", () => {
    expect(hasPending([{ status: "accepted" }, { status: "processing" }])).toBe(true);
    // «Нужен исход» ждёт менеджера до суток: опрашивать сервер ради него незачем.
    expect(hasPending([{ status: "awaiting_outcome" }])).toBe(false);
    expect(hasPending([{ status: "accepted" }, { status: "duplicate" }])).toBe(false);
    expect(hasPending([])).toBe(false);
  });

  it("shows only the time for today and the date for earlier days", () => {
    const now = new Date(2026, 9, 23, 18, 0);
    expect(formatMoment(new Date(2026, 9, 23, 14, 32).toISOString(), now)).toBe("14:32");
    expect(formatMoment(new Date(2026, 9, 22, 9, 5).toISOString(), now)).toBe("22.10, 09:05");
  });

  it("explains duplicates and rejections, otherwise shows the chat tail", () => {
    const now = new Date(2026, 9, 23, 18, 0);
    expect(submissionNote(submission(), now)).toEqual({ text: "чат …a1b2c3", reason: false });
    expect(
      submissionNote(
        submission({ status: "duplicate", duplicate_received_at: new Date(2026, 9, 21, 10, 0).toISOString() }),
        now,
      ),
    ).toEqual({ text: "уже загружен 21.10, 10:00", reason: true });
    expect(
      submissionNote(submission({ status: "rejected", reason_text: "Нужен чёткий скриншот всего окна" }), now),
    ).toEqual({
      text: "Нужен чёткий скриншот всего окна",
      reason: true,
    });
    expect(submissionNote(submission({ status: "awaiting_outcome", outcome: null }), now)).toEqual({
      text: "Выберите исход",
      reason: true,
    });
    expect(submissionNote(submission({ status: "processing", fields: null }), now)).toBeNull();
  });

  it("counts down in minutes and seconds", () => {
    expect(formatCountdown(15 * 60_000)).toBe("15:00");
    expect(formatCountdown(61_000)).toBe("01:01");
    expect(formatCountdown(500)).toBe("00:01");
    expect(formatCountdown(-1)).toBe("00:00");
  });

  it("formats file sizes", () => {
    expect(formatBytes(15 * 1024 * 1024)).toBe("15 МБ");
    expect(formatBytes(2.5 * 1024 * 1024)).toBe("2,5 МБ");
    expect(formatBytes(300 * 1024)).toBe("300 КБ");
  });
  it("labels the manager timezone from the list", () => {
    const zones = [{ id: "Europe/Moscow", label: "Москва, UTC+3", utc_offset_minutes: 180 }];
    expect(zoneLabel({ timezone: "Europe/Moscow", zones })).toBe("Москва, UTC+3");
    expect(zoneLabel({ timezone: "Asia/Tokyo", zones })).toBe("Asia/Tokyo");
  });
});
