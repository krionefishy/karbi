import { describe, expect, it } from "vitest";

import {
  agreementBadge,
  buildResolve,
  comparePair,
  confidenceBadge,
  duplicateTargets,
  formatAge,
  formatStorage,
  keyAction,
  managersCsv,
  managerSignals,
  noteRequired,
  pairTarget,
  pipelineAlerts,
  queuePeriodRange,
  selectAfterRemoval,
  stepSelection,
  suspicionBadge,
} from "./admin";
import type { AdminSubmission, ManagerStats, Match, Pipeline } from "./types";

const match = (other_id: string, kind: Match["kind"], score: number, minutesAgo: number, username = "Анна"): Match => ({
  other_id,
  other_user: { id: `u-${username}`, username },
  other_received_at: new Date(Date.UTC(2026, 9, 22, 10, 0) - minutesAgo * 60_000).toISOString(),
  other_outcome: "deleted",
  other_verdict: "accepted",
  kind,
  score,
  thumb_url: null,
});

const item = (overrides: Partial<AdminSubmission> = {}): Pick<
  AdminSubmission,
  "verdict" | "audit_requested" | "matches" | "duplicate_of" | "user" | "outcome"
> => ({
  verdict: "needs_review",
  audit_requested: false,
  matches: [],
  duplicate_of: null,
  user: { id: "u-igor", username: "Игорь" },
  outcome: "positive",
  ...overrides,
});

describe("решения", () => {
  it("maps «Засчитать» onto the decision the verdict allows", () => {
    expect(buildResolve(item(), "approve")).toEqual({ decision: "approve", note: "" });
    expect(buildResolve(item({ verdict: "duplicate" }), "approve").decision).toBe("not_duplicate");
    expect(buildResolve(item({ verdict: "accepted", audit_requested: true }), "approve").decision).toBe("audit_ok");
  });

  it("sends the duplicate target and trimmed note", () => {
    expect(buildResolve(item(), "duplicate", { target: "s-1", note: "  тот же чат " })).toEqual({
      decision: "duplicate_of",
      duplicate_of: "s-1",
      note: "тот же чат",
    });
  });

  it("requires a note to decline and to take back an accepted screenshot", () => {
    expect(noteRequired("decline", item())).toBe(true);
    expect(noteRequired("duplicate", item())).toBe(false);
    expect(noteRequired("duplicate", item({ verdict: "accepted" }))).toBe(true);
    expect(noteRequired("approve", item({ verdict: "accepted" }))).toBe(false);
  });
});

describe("клавиши", () => {
  const key = (code: string, keyValue = "", extra: object = {}) => keyAction({ code, key: keyValue, ...extra });

  it("reads physical keys so the Russian layout works too", () => {
    expect(key("KeyA", "ф")).toBe("approve");
    expect(key("KeyD", "в")).toBe("decline");
    expect(key("KeyN", "т")).toBe("duplicate");
    expect(key("KeyY", "н")).toBe("duplicate");
    expect(key("ArrowRight")).toBe("next");
    expect(key("ArrowLeft")).toBe("prev");
    expect(key("KeyQ", "q")).toBeNull();
  });

  it("stays out of the way while typing or with modifiers", () => {
    const textarea = document.createElement("textarea");
    const select = document.createElement("select");
    expect(key("KeyA", "a", { target: textarea })).toBeNull();
    expect(key("ArrowRight", "", { target: select })).toBeNull();
    expect(key("KeyA", "a", { metaKey: true })).toBeNull();
    expect(key("KeyD", "d", { ctrlKey: true })).toBeNull();
  });

  it("moves through the queue and lands on the neighbour after a decision", () => {
    const ids = ["a", "b", "c"];
    expect(stepSelection(ids, "a", 1)).toBe("b");
    expect(stepSelection(ids, "c", 1)).toBe("c");
    expect(stepSelection(ids, "a", -1)).toBe("a");
    expect(stepSelection(ids, null, 1)).toBe("a");
    expect(selectAfterRemoval(ids, "b")).toBe("c");
    expect(selectAfterRemoval(ids, "c")).toBe("b");
    expect(selectAfterRemoval(["a"], "a")).toBeNull();
  });
});

describe("сравнение пары", () => {
  it("groups matches per submission, earliest first", () => {
    const targets = duplicateTargets(
      item({
        matches: [match("late", "chat_text", 0.8, 5, "Олег"), match("first", "case_key", 1, 60), match("first", "chat_text", 0.97, 60)],
      }),
    );
    expect(targets.map((target) => target.id)).toEqual(["first", "late"]);
    expect(targets[0].kinds).toEqual({ case_key: 1, chat_text: 0.97 });
  });

  it("prefers the submission already named in duplicate_of", () => {
    const value = item({ matches: [match("first", "case_key", 1, 60), match("other", "near_image", 0.91, 5)], duplicate_of: "other" });
    expect(pairTarget(value)?.id).toBe("other");
  });

  it("builds the feature table with badges", () => {
    const value = item({ matches: [match("first", "case_key", 1, 60), match("first", "chat_text", 0.97, 60), match("first", "near_image", 0.91, 60)] });
    const rows = comparePair(value, pairTarget(value)!);
    expect(rows.map((row) => [row.label, row.badge?.label ?? row.note])).toEqual([
      ["Чат", "совпал"],
      ["Время отзыва", "совпало"],
      ["Текст", "0,97"],
      ["Картинка (хеш)", "похожа, 0,91"],
      ["Подписи", "Анна: Удален · Игорь: Полож"],
    ]);
  });

  it("says plainly what did not match", () => {
    const value = item({ matches: [match("first", "exact_file", 1, 60)] });
    const rows = comparePair(value, pairTarget(value)!);
    expect(rows[0].badge?.label).toBe("не совпал");
    expect(rows[3].badge).toEqual({ label: "тот же файл", tone: "positive" });
  });
});

describe("бейджи", () => {
  it("names suspicion levels", () => {
    expect(suspicionBadge("high")).toEqual({ label: "Высокий", tone: "negative" });
    expect(suspicionBadge("medium")?.label).toBe("Средний");
    expect(suspicionBadge("low")?.label).toBe("Низкий");
    expect(suspicionBadge(null)).toBeNull();
  });

  it("colours confidence and VLM/OCR agreement", () => {
    expect(confidenceBadge(0.78)).toEqual({ label: "0,78", tone: "warning" });
    expect(confidenceBadge(0.95)?.tone).toBe("positive");
    expect(confidenceBadge(null)).toBeNull();
    expect(agreementBadge(0.93)?.label).toBe("VLM = OCR");
    expect(agreementBadge(0.6)).toEqual({ label: "VLM ≠ OCR, 0,60", tone: "warning" });
  });
});

const stats = (overrides: Partial<ManagerStats> = {}): ManagerStats => ({
  user: { id: "u1", username: "Игорь" },
  total: 44,
  accepted: 30,
  positive: 12,
  amended: 7,
  deleted: 11,
  duplicate: 9,
  needs_review: 5,
  rejected: 0,
  processing: 0,
  duplicate_rate: 0.2,
  review_rate: 0.114,
  reject_rate: 0,
  last_submission_at: null,
  flags: [],
  ...overrides,
});

describe("менеджеры", () => {
  it("turns flags into conversation signals", () => {
    expect(managerSignals(stats())).toEqual([{ label: "Норма", tone: "positive" }]);
    expect(managerSignals(stats({ flags: ["high_duplicate_rate", "upload_burst"] })).map((badge) => badge.label)).toEqual([
      "Дублей 20%",
      "Всплеск загрузок",
    ]);
    expect(managerSignals(stats({ flags: ["high_review_rate"], review_rate: 0.17 }))[0].label).toBe("Ревью 17%");
  });

  it("exports CSV for Excel with quoting", () => {
    const csv = managersCsv([stats({ user: { id: "u2", username: 'Анна; "старшая"' }, flags: ["high_duplicate_rate"] })], {
      from: "2026-10-01",
      to: "2026-10-23",
    });
    expect(csv.startsWith("﻿Менеджер;Всего;")).toBe(true);
    expect(csv).toContain('"Анна; ""старшая""";44;30;12;7;11;9;5;0;20%;11%;Дублей 20%;2026-10-01 — 2026-10-23');
  });
});

const pipeline = (overrides: Partial<Pipeline> = {}): Pipeline => ({
  mode: "shadow",
  telegram_enabled: false,
  stages: { processing: 2, extracted: 0 },
  oldest_processing_age_s: 8,
  p50_s: 9,
  p95_s: 24,
  vlm_error_rate_1h: 0,
  extraction_failed_today: 0,
  tokens_today: 412_000,
  storage: { objects: { stored: 1240 }, stored_bytes: 1_600_000_000, oldest_staged_age_s: 3, staging_used_percent: 12 },
  channels_today: { extension: 30, telegram: 0, web: 4 },
  ...overrides,
});

describe("конвейер", () => {
  it("stays quiet when everything is fine", () => {
    expect(pipelineAlerts(pipeline())).toEqual([]);
  });

  it("warns about staging, VLM errors and a stuck queue", () => {
    const alerts = pipelineAlerts(
      pipeline({
        vlm_error_rate_1h: 0.25,
        oldest_processing_age_s: 660,
        storage: { objects: {}, stored_bytes: 0, oldest_staged_age_s: 1000, staging_used_percent: 91.4 },
      }),
    );
    expect(alerts.map((alert) => alert.key)).toEqual(["staging", "staged-age", "vlm", "queue"]);
    expect(alerts[0].title).toBe("Временное хранилище заполнено на 91%");
    expect(alerts[3].title).toBe("Скриншот ждёт вердикта 11 мин");
  });

  it("formats ages and period ranges", () => {
    expect(formatAge(8)).toBe("8 с");
    expect(formatAge(3900)).toBe("1 ч 5 мин");
    expect(formatAge(null)).toBe("—");
    expect(formatStorage(1.6 * 1024 ** 3)).toBe("1,6 ГБ");
    expect(formatStorage(0)).toBe("0 МБ");
    expect(queuePeriodRange("all")).toEqual({});
    expect(queuePeriodRange("day", new Date("2026-10-23T09:00:00Z"))).toEqual({ from: "2026-10-23", to: "2026-10-23" });
  });
});
