import { describe, expect, it } from "vitest";

import { staleRefreshError } from "./refresh";

const failed = { status: "error" as const, in_progress: false, finished_at: "2026-09-16T13:12:00Z", error: "WB просит ждать" };

describe("staleRefreshError", () => {
  it("показывает ошибку, пока после неё не было удачного сбора", () => {
    expect(staleRefreshError(failed, null)).toBe("WB просит ждать");
    expect(staleRefreshError(failed, "2026-09-16T12:00:00Z")).toBe(
      "WB просит ждать",
    );
  });

  it("прячет ошибку, если плановый сбор прошёл позже", () => {
    expect(staleRefreshError(failed, "2026-09-16T15:04:00Z")).toBeNull();
  });

  it("молчит без ошибки и во время обновления", () => {
    expect(
      staleRefreshError({ ...failed, status: "success" }, null),
    ).toBeNull();
    expect(
      staleRefreshError({ ...failed, in_progress: true }, null),
    ).toBeNull();
    expect(staleRefreshError(undefined, null)).toBeNull();
    expect(staleRefreshError({ ...failed, error: null }, null)).toBe(
      "неизвестная ошибка",
    );
  });
});
