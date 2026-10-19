import { describe, expect, it } from "vitest";

import { needsDecision, statusLine, totalDialogs, verdictNote } from "./format";

describe("supportBot format", () => {
  it("общий выключатель и причина остановки перекрывают режим", () => {
    const base = { mode: "live" as const, paused_reason: null, globally_paused: false };
    expect(statusLine(base)).toBe("Бот отправляет сообщения");
    expect(statusLine({ ...base, mode: "dry_run" })).toMatch(/Сухой прогон/);
    expect(statusLine({ ...base, paused_reason: "ключ без права записи" })).toMatch(/ключ без права записи/);
    expect(statusLine({ ...base, paused_reason: "ключ", globally_paused: true })).toMatch(/общим выключателем/);
  });

  it("канарейка называет долю диалогов", () => {
    const line = statusLine({ mode: "canary", paused_reason: null, globally_paused: false, canary_percent: 10 });
    expect(line).toMatch(/10% диалогов/);
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
