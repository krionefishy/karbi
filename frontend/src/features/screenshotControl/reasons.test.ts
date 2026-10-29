import { describe, expect, it } from "vitest";

import typesSource from "./types.ts?raw";
import { primaryReason, REASON_GROUPS, REASON_TEXT, reasonsLine } from "./reasons";
import type { Reason } from "./types";

/** Коды из union ReasonCode в types.ts — чтобы новый код с бэкенда не прошёл без русской фразы. */
function reasonCodesFromTypes(): string[] {
  const block = typesSource.match(/export type ReasonCode =([^;]+);/);
  if (!block) throw new Error("ReasonCode не найден в types.ts");
  return [...block[1].matchAll(/"([a-z_]+)"/g)].map((match) => match[1]);
}

const reason = (code: Reason["code"], severity: Reason["severity"]): Reason => ({
  code,
  severity,
  detail: null,
  other_submission_id: null,
});

describe("словарь причин", () => {
  it("has a phrase for every ReasonCode and nothing extra", () => {
    const codes = reasonCodesFromTypes();
    expect(codes.length).toBeGreaterThan(20);
    expect(Object.keys(REASON_TEXT).sort()).toEqual([...codes].sort());
    for (const code of codes) expect(REASON_TEXT[code as keyof typeof REASON_TEXT].trim()).not.toBe("");
  });

  it("puts every code into exactly one filter group", () => {
    const grouped = REASON_GROUPS.flatMap((group) => group.codes);
    expect([...grouped].sort()).toEqual(reasonCodesFromTypes().sort());
    expect(new Set(grouped).size).toBe(grouped.length);
  });

  it("leads with the most severe reason", () => {
    const reasons = [reason("weak_key", "warn"), reason("cross_user_duplicate", "high"), reason("audit_sample", "info")];
    expect(primaryReason(reasons)?.code).toBe("cross_user_duplicate");
    expect(reasonsLine(reasons)).toBe(
      "тот же отзыв уже прислал другой менеджер; слабый ключ, короткий текст; случайная проверка",
    );
    expect(reasonsLine([])).toBe("без причины");
  });
});
