import { describe, expect, it } from "vitest";

import {
  GATE_NOTE,
  REPLY_LABEL,
  REPLY_ORDER,
  armRates,
  cumulative,
  dailySeries,
  dailyTip,
  dayBars,
  formatCount,
  formatPoints,
  formatRatio,
  formatShare,
  rateTip,
  tipText,
  formatMinutes,
  formatInterval,
  paceError,
  paceExample,
  groupChecks,
  groupLabel,
  incidentDuration,
  severityLabel,
  severityTone,
  sortChecks,
  workerStale,
  mailingNote,
  productCounts,
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
    expect(statusLine({ ...base, mode: "stopped" })).toMatch(/Рассылка не включена/);
    expect(statusLine({ ...base, paused_reason: "ключ без права записи" })).toMatch(/ключ без права записи/);
    expect(statusLine({ ...base, paused_reason: "ключ", globally_paused: true })).toMatch(/общим выключателем/);
  });

  it("сумма диалогов не падает на пустой сводке", () => {
    expect(totalDialogs({ counts: {} })).toBe(0);
    expect(totalDialogs({ counts: { in_campaign: 3, awaiting_us: 2 } })).toBe(5);
  });

  it("вердикт модели подписывается, причина идёт в скобках", () => {
    const none = { verdict: null, verdict_reason: null };
    expect(verdictNote(none)).toBeNull();
    expect(verdictNote({ verdict: "closing", verdict_reason: "заявка одобрена" })).toBe(
      "ИИ: диалог завершён (заявка одобрена)",
    );
    expect(verdictNote({ verdict: "open", verdict_reason: null })).toBe("ИИ: ждёт ответа");
  });

  it("подпись под тумблером говорит, откуда решение, а у ручного называет срок", () => {
    const base = { on: true, locked: false, manual_until: null };
    expect(mailingNote({ ...base, source: "model", reason: "verdict_open" })).toBe("по решению модели");
    expect(mailingNote({ ...base, on: false, source: "model", reason: "verdict_pending" })).toBe("ждёт оценки модели");
    expect(mailingNote({ ...base, on: false, source: "product", reason: "product_off", locked: true })).toBe(
      "выключен товаром",
    );
    const manual = mailingNote({ ...base, source: "manual", reason: "manual_on", manual_until: "2026-11-09T12:20:00+00:00" });
    expect(manual).toMatch(/^включён вручную до 09\.11, /);
  });

  it("счётчик товара: у включённого сколько пишем, у выключенного сколько диалогов", () => {
    expect(productCounts({ enabled: true, writing: 3, total: 12 })).toBe("пишем 3 из 12");
    expect(productCounts({ enabled: false, writing: 0, total: 5 })).toBe("выключен, диалогов 5");
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
    expect(groupLabel("panel")).toBe("Панель решений");
    expect(groupLabel("safety")).toBe("Безопасность ответов");
  });

  it("проверки: crit, warn, nodata, ok; порядок внутри уровня сохраняется", () => {
    const check = (
      id: string,
      severity: "ok" | "warn" | "crit" | "nodata",
      group: "delivery" | "model" | "panel" | "safety" = "delivery",
    ) => ({
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
    const groups = groupChecks([
      check("a", "ok"),
      check("b", "crit", "model"),
      check("c", "crit"),
      check("d", "warn", "safety"),
      check("e", "nodata", "panel"),
    ]);
    expect(groups.map((item) => item.group)).toEqual(["delivery", "model", "panel", "safety"]);
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

  describe("подсказки графиков", () => {
    const point = {
      date: "2026-11-08",
      started_live: 20,
      started_control: 10,
      eligible48_live: 20,
      eligible48_control: 10,
      replied48_live: 12,
      replied48_control: 4,
      replies_live: 7,
      replies_control: 3,
    };

    it("форматирует числа по-русски", () => {
      expect(formatShare(0.6)).toBe("60,0%");
      expect(formatShare(0.0375)).toBe("3,8%");
      expect(formatShare(0)).toBe("0%");
      expect(formatShare(1)).toBe("100%");
      expect(formatCount(1234)).toBe(new Intl.NumberFormat("ru-RU").format(1234));
      expect(formatRatio(12, 20)).toBe("12 из 20 · 60,0%");
      expect(formatRatio(0, 0)).toBe("нет данных");
      expect(formatPoints(0.2)).toBe("+20,0 п. п.");
      expect(formatPoints(-0.025)).toBe("−2,5 п. п.");
      expect(formatPoints(0)).toBe("0 п. п.");
    });

    it("подсказка по дням: числа, накопленный итог и доля 48 ч", () => {
      const tip = dailyTip(point, 42, { mature: true, marker: true });
      expect(tip.title).toContain("08.11");
      expect(tip.title).toContain("режим 100%");
      const text = tipText(tip);
      expect(text).toContain("С напоминанием: 7");
      expect(text).toContain("Контроль: 3");
      expect(text).toContain("Накопленный итог: 42");
      expect(text).toContain("12 из 20 · 60,0%");
    });

    it("подсказка по дням: пустой день и незакрытое окно", () => {
      const empty = { ...point, started_live: 0, started_control: 0, eligible48_live: 0, eligible48_control: 0, replied48_live: 0, replied48_control: 0, replies_live: 0, replies_control: 0 };
      expect(dailyTip(empty, 5, { mature: true, marker: false }).rows[0]).toEqual({ label: "За день", value: "нет данных" });
      const fresh = tipText(dailyTip(point, 42, { mature: false, marker: false }));
      expect(fresh).not.toContain("ответили за 48 ч");
    });

    it("подсказка доли 48 ч: интервал, разница, нет данных, незакрытое окно", () => {
      const tip = rateTip(point, { mature: true, marker: false });
      expect(tip.rows[0].value).toBe("12 из 20 · 60,0%");
      expect(tip.rows[0].hint).toMatch(/^95%: \d+,\d–\d+,\d%$/);
      expect(tip.rows[1].value).toBe("4 из 10 · 40,0%");
      expect(tip.rows[2]).toEqual({ label: "Разница", value: "+20,0 п. п." });
      const noControl = rateTip({ ...point, eligible48_control: 0, replied48_control: 0 }, { mature: true, marker: false });
      expect(noControl.rows[1].value).toBe("нет данных");
      expect(noControl.rows).toHaveLength(2);
      const fresh = rateTip(point, { mature: false, marker: false });
      expect(fresh.rows[0].value).toBe("окно 48 ч ещё не закрыто");
      expect(fresh.rows).toHaveLength(2);
    });
  });
});

describe("pace", () => {
  const pace = { interval_minutes: 120, jitter_min_minutes: 1, jitter_max_minutes: 3 };

  it("writes the interval and the example", () => {
    expect(formatInterval(45)).toBe("45 мин");
    expect(formatInterval(90)).toBe("1 ч 30 мин");
    expect(formatInterval(120)).toBe("2 ч");
    expect(paceExample(pace)).toBe("например, через 2 ч 1–3 мин");
    expect(paceExample({ ...pace, jitter_max_minutes: 1 })).toBe("например, через 2 ч 1 мин");
  });

  it("checks the same limits as the server", () => {
    expect(paceError(pace)).toBeNull();
    expect(paceError({ ...pace, interval_minutes: 9 })).toContain("от 10 до 1440");
    expect(paceError({ ...pace, interval_minutes: 1441 })).toContain("от 10 до 1440");
    expect(paceError({ ...pace, jitter_min_minutes: -1 })).toContain("отрицательной");
    expect(paceError({ ...pace, jitter_max_minutes: 0 })).toContain("меньше");
    expect(paceError({ ...pace, jitter_max_minutes: 241 })).toContain("больше 240");
    expect(paceError({ ...pace, interval_minutes: Number.NaN })).toContain("целые");
  });
});
