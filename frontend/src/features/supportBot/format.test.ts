import { describe, expect, it } from "vitest";

import { statusLine, totalDialogs } from "./format";

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
});
