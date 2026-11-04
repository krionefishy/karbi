import { describe, expect, it } from "vitest";

import { buildGroups, collapsedSnapshots, filterGroups, pluralizeDummies, sortGroupsByMovement } from "./groups";
import type { ProductReviewHistory } from "./types";

function item(id: string, extra: Partial<ProductReviewHistory> = {}): ProductReviewHistory {
  return {
    id,
    article: id,
    vendor_code: `vc-${id}`,
    name: `Товар ${id}`,
    imt_id: null,
    brand: "",
    subject_id: null,
    subject_name: "Шуруповерты",
    photo_url: "",
    state: "active",
    snapshots: [],
    card_snapshots: [],
    photo_count: 0,
    rating: null,
    rating_source: null,
    group_id: null,
    group_role: null,
    group_manual: false,
    group_snapshots: [],
    ...extra,
  };
}

const day = (date: string, five: number) => ({ date, ratings: { 1: 0, 2: 0, 3: 0, 4: 0, 5: five } });

describe("buildGroups", () => {
  it("puts the main product first whatever order the members came in", () => {
    const groups = buildGroups([
      item("b", { group_id: "g", group_role: "member" }),
      item("a", { group_id: "g", group_role: "main" }),
      item("c"),
    ]);
    expect(groups.map((group) => [group.main.id, group.members.map((member) => member.id)])).toEqual([
      ["a", ["b"]],
      ["c", []],
    ]);
  });
});

describe("collapsedSnapshots", () => {
  it("uses the group total for a group and the product itself otherwise", () => {
    const [group, single] = buildGroups([
      item("a", { group_id: "g", group_role: "main", snapshots: [day("2026-11-04", 1)], group_snapshots: [day("2026-11-04", 9)] }),
      item("b", { group_id: "g", group_role: "member" }),
      item("c", { snapshots: [day("2026-11-04", 3)] }),
    ]);
    expect(collapsedSnapshots(group)[0].ratings[5]).toBe(9);
    expect(collapsedSnapshots(single)[0].ratings[5]).toBe(3);
  });
});

describe("filterGroups", () => {
  const groups = buildGroups([
    item("main", { group_id: "g", group_role: "main", vendor_code: "Шуруповерт основной" }),
    item("dummy", { group_id: "g", group_role: "member", vendor_code: "шуруповёрт7" }),
    item("other", { vendor_code: "Болгарка" }),
  ]);

  it("keeps a group when only a dummy matches and opens it", () => {
    const selection = filterGroups(groups, { query: "шуруповёрт7", subjects: [] });
    expect(selection.groups.map((group) => group.id)).toEqual(["g"]);
    expect([...selection.revealed]).toEqual(["g"]);
  });

  it("does not open a group whose main product matches", () => {
    const selection = filterGroups(groups, { query: "основной", subjects: [] });
    expect(selection.groups).toHaveLength(1);
    expect(selection.revealed.size).toBe(0);
  });
});

describe("sortGroupsByMovement", () => {
  it("ranks a group by the movement of its total", () => {
    const groups = buildGroups([
      item("quiet", { snapshots: [day("2026-11-03", 5), day("2026-11-04", 6)] }),
      item("main", {
        group_id: "g",
        group_role: "main",
        snapshots: [day("2026-11-03", 5), day("2026-11-04", 5)],
        group_snapshots: [day("2026-11-03", 50), day("2026-11-04", 60)],
      }),
      item("dummy", { group_id: "g", group_role: "member" }),
    ]);
    expect(sortGroupsByMovement(groups, "2026-11-04").map((group) => group.main.id)).toEqual(["main", "quiet"]);
  });
});

describe("pluralizeDummies", () => {
  it.each([
    [1, "болванка"],
    [3, "болванки"],
    [5, "болванок"],
    [12, "болванок"],
    [21, "болванка"],
  ])("%i → %s", (count, word) => expect(pluralizeDummies(count)).toBe(word));
});
