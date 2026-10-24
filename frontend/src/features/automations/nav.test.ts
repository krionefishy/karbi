import { describe, expect, it } from "vitest";

import { automationNav } from "./nav";

describe("automation navigation", () => {
  it("has a unique id per service, the same ids that access is granted by", () => {
    const ids = automationNav.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
