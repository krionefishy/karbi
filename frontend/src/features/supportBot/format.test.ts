import { describe, expect, it } from "vitest";

import {
  GATE_NOTE,
  REPLY_LABEL,
  REPLY_ORDER,
  armRates,
  cumulative,
  dailySeries,
  dayBars,
  formatMinutes,
  groupChecks,
  groupLabel,
  incidentDuration,
  severityLabel,
  severityTone,
  sortChecks,
  workerStale,
  needsDecision,
  percent,
  replyLabel,
  replyTone,
  sortedCategories,
  statusLine,
  totalDialogs,
  verdictNote,
  wilson,
  wilsonText,
} from "./format";

describe("supportBot format", () => {
  it("общий выключатель и причина остановки перекрывают режим", () => {
    const base = { mode: "live" as const, paused_reason: null, globally_paused: false };
    expect(statusLine(base)).toBe("Бот отправляет сообщения");
    expect(statusLine({ ...base, mode: "dry_run" })).toMatch(/Сухой прогон/);
    expect(statusLine({ ...base, paused_reason: "ключ без права записи" })).toMatch(/ключ без права записи/);
    expect(statusLine({ ...base, paused_reason: "ключ", globally_paused: true })).toMatch(/общим выключателем/);
  });

  it("сумма диалогов не падает на пустой сводке", () => {
    expect(totalDialogs({ counts: {} })).toBe(0);
    expect(totalDialogs({ counts: { in_campaign: 3, awaiting_us: 2 } })).toBe(5);
  });

  it("решение менеджера важнее вердикта, а причина модели идёт в скобках", () => {
    const none = { verdict: null, verdict_reason: null, manual_verdict: null };
    expect(verdictNote(none)).toBeNull();
    expect(verdictNote({ ...none, verdict: "closing", verdict_reason: "заявка одобрена" })).toBe(
      "ИИ: диалог завершён (заявка одобрена)",
    );
    expect(verdictNote({ ...none, verdict: "open" })).toBe("ИИ: ждёт ответа");
    expect(verdictNote({ verdict: "closing", verdict_reason: "x", manual_verdict: "allow" })).toBe("вручную: писать");
  });

  it("отметка «писать / не писать» нужна, пока нейросеть не подтвердила, что покупателя ждут", () => {
    const base = { state: "in_campaign" as const, verdict: "closing" as const, manual_verdict: null };
    expect(needsDecision(base, "enforce")).toBe(true);
    expect(needsDecision({ ...base, verdict: "unclear" }, "shadow")).toBe(true);
    expect(needsDecision({ ...base, verdict: null }, "enforce")).toBe(true);
    expect(needsDecision({ ...base, verdict: "open" }, "enforce")).toBe(false);
    expect(needsDecision({ ...base, verdict: "open", manual_verdict: "deny" }, "enforce")).toBe(true);
    expect(needsDecision(base, "off")).toBe(false);
    expect(needsDecision({ ...base, state: "awaiting_buyer" }, "enforce")).toBe(false);
  });
});

describe("supportBot replies format", () => {
  it("у каждого класса есть подпись и место в порядке", () => {
    expect(REPLY_ORDER).toHaveLength(15);
    expect(new Set(REPLY_ORDER).size).toBe(15);
    for (const bucket of REPLY_ORDER) expect(REPLY_LABEL[bucket]).toBeTruthy();
    expect(replyLabel("threat")).toBe("Угрозы, жалобы, агрессия");
    expect(replyLabel("something_new")).toBe("something_new");
    expect(GATE_NOTE.off).toBe("Слой ответов выключен");
  });

  it("тона классов", () => {
    expect(replyTone("threat")).toBe("stop");
    expect(replyTone("stop_request")).toBe("warn");
    for (const bucket of ["refuses", "not_helped", "noise"] as const) expect(replyTone(bucket)).toBe("go");
    for (const bucket of ["thanks", "question", "unclear", "stopword", "undetermined"] as const) {
      expect(replyTone(bucket)).toBe("neutral");
    }
  });

  it("процент с одним знаком и прочерк при нуле", () => {
    expect(percent(1, 3)).toBe("33.3%");
    expect(percent(0, 5)).toBe("0.0%");
    expect(percent(5, 0)).toBe("—");
  });

  it("интервал Уилсона", () => {
    expect(wilson(0, 0)).toEqual([0, 0]);
    const [low, high] = wilson(50, 100);
    expect(low).toBeCloseTo(0.4038, 3);
    expect(high).toBeCloseTo(0.5962, 3);
    const [l0, h0] = wilson(0, 10);
    expect(l0).toBe(0);
    expect(h0).toBeCloseTo(0.2775, 3);
    const [l1, h1] = wilson(10, 10);
    expect(l1).toBeCloseTo(0.7225, 3);
    expect(h1).toBe(1);
    expect(wilsonText(50, 100)).toBe("40.4–59.6%");
    expect(wilsonText(1, 0)).toBe("—");
  });

  it("накопленная сумма", () => {
    expect(cumulative([])).toEqual([]);
    expect(cumulative([1, 0, 4, 2])).toEqual([1, 1, 5, 7]);
  });

  it("доли ответивших из ArmStats", () => {
    expect(armRates({ dialogs: 9, eligible24: 10, replied24: 3, eligible48: 8, replied48: 4 })).toEqual({
      rate24: 0.3,
      rate48: 0.5,
    });
    expect(armRates({ dialogs: 0, eligible24: 0, replied24: 0, eligible48: 0, replied48: 0 })).toEqual({
      rate24: null,
      rate48: null,
    });
  });

  it("ряды по дням: доля 48 ч только там, где были старты", () => {
    const series = dailySeries([
      { date: "2026-10-01", started_live: 4, started_control: 0, eligible48_live: 4, eligible48_control: 0, replied48_live: 1, replied48_control: 0, replies_live: 2, replies_control: 1 },
      { date: "2026-10-02", started_live: 0, started_control: 2, eligible48_live: 0, eligible48_control: 2, replied48_live: 0, replied48_control: 1, replies_live: 0, replies_control: 3 },
    ]);
    expect(series.dates).toEqual(["2026-10-01", "2026-10-02"]);
    expect(series.live).toEqual([2, 0]);
    expect(series.control).toEqual([1, 3]);
    expect(series.cumulative).toEqual([3, 6]);
    expect(series.rate48Live).toEqual([0.25, null]);
    expect(series.rate48Control).toEqual([null, 0.5]);
  });

  it("классы сортируются по порядку и без нулей", () => {
    const result = sortedCategories([
      { category: "question", count: 2 },
      { category: "threat", count: 1 },
      { category: "noise", count: 0 },
      { category: "undetermined", count: 5 },
    ]);
    expect(result.map((item) => item.category)).toEqual(["threat", "question", "undetermined"]);
  });

  it("задержка в минутах", () => {
    expect(formatMinutes(null)).toBe("—");
    expect(formatMinutes(0)).toBe("0 мин");
    expect(formatMinutes(5.4)).toBe("5 мин");
    expect(formatMinutes(60)).toBe("1 ч");
    expect(formatMinutes(125)).toBe("2 ч 05 мин");
    expect(formatMinutes(1440)).toBe("1 д");
    expect(formatMinutes(1620)).toBe("1 д 3 ч");
  });

  it("подпись и тон уровня", () => {
    expect(severityLabel("ok")).toBe("В норме");
    expect(severityLabel("warn")).toBe("Внимание");
    expect(severityLabel("crit")).toBe("Проблема");
    expect(severityLabel("nodata")).toBe("Мало данных");
    expect(severityTone("ok")).toBe("go");
    expect(severityTone("warn")).toBe("warn");
    expect(severityTone("crit")).toBe("stop");
    expect(severityTone("nodata")).toBe("neutral");
    expect(groupLabel("delivery")).toBe("Рассылка");
    expect(groupLabel("model")).toBe("Нейросеть");
    expect(groupLabel("safety")).toBe("Безопасность ответов");
  });

  it("проверки: crit, warn, nodata, ok; порядок внутри уровня сохраняется", () => {
    const check = (id: string, severity: "ok" | "warn" | "crit" | "nodata", group: "delivery" | "model" = "delivery") => ({
      id,
      group,
      title: id,
      severity,
      value: null,
      threshold: "",
      hint: "",
    });
    const sorted = sortChecks([check("a", "ok"), check("b", "nodata"), check("c", "warn"), check("d", "crit"), check("e", "warn")]);
    expect(sorted.map((item) => item.id)).toEqual(["d", "c", "e", "b", "a"]);
    const groups = groupChecks([check("a", "ok"), check("b", "crit", "model"), check("c", "crit")]);
    expect(groups.map((item) => item.group)).toEqual(["delivery", "model"]);
    expect(groups[0].checks.map((item) => item.id)).toEqual(["c", "a"]);
  });

  it("длительность инцидента", () => {
    const now = Date.parse("2026-10-29T12:00:00Z");
    expect(
      incidentDuration({ started_at: "2026-10-29T08:00:00Z", ended_at: "2026-10-29T10:15:00Z" }, now),
    ).toBe("2 ч 15 мин");
    expect(incidentDuration({ started_at: "2026-10-29T11:20:00Z", ended_at: null }, now)).toBe("идёт 40 мин");
    expect(incidentDuration({ started_at: "bad", ended_at: null }, now)).toBe("—");
  });

  it("полосы по дням: минимум 7 дней, пустые как нули", () => {
    const full = Array.from({ length: 7 }, (_, i) => ({ date: `2026-10-0${i + 1}`, warn_minutes: i, crit_minutes: 0 }));
    expect(dayBars(full)).toHaveLength(7);
    const short = dayBars([{ date: "2026-10-29", warn_minutes: 10, crit_minutes: 5 }]);
    expect(short).toHaveLength(7);
    expect(short[0]).toEqual({ date: "2026-10-23", warn: 0, crit: 0 });
    expect(short[6]).toEqual({ date: "2026-10-29", warn: 10, crit: 5 });
    expect(dayBars([]).every((bar) => bar.warn === 0 && bar.crit === 0)).toBe(true);
    expect(dayBars([])).toHaveLength(7);
  });

  it("воркер не отвечает дольше 30 минут", () => {
    const now = Date.parse("2026-10-29T12:00:00Z");
    expect(workerStale(null, now)).toBe(false);
    expect(workerStale("2026-10-29T11:45:00Z", now)).toBe(false);
    expect(workerStale("2026-10-29T11:20:00Z", now)).toBe(true);
  });
});
