import { describe, expect, it } from "vitest";

import {
  applyFilters,
  collectSubjects,
  hasActiveFilters,
  matchesQuery,
  NO_SUBJECT,
  subjectOf,
} from "./filters";

const item = (article: string, vendor_code: string, subject_name: string) => ({
  article,
  vendor_code,
  subject_name,
});

const catalog = [
  item("1272526845", "перфоратор спутник 2", "Перфораторы"),
  item("1261107992", "Пылесос беспроводной", "Пылесосы"),
  item("1261123670", "Компрессор автомобильный", "Насосы автомобильные"),
  item("945829620", "Шуруповерт22", "Шуруповерты"),
  item("1272524122", "пароочиститель спутник 2", ""),
];

describe("matchesQuery", () => {
  it("finds a WB article by a fragment of it", () => {
    expect(matchesQuery(catalog[0], "5268")).toBe(true);
    expect(matchesQuery(catalog[0], "1272526845")).toBe(true);
    expect(matchesQuery(catalog[0], "999")).toBe(false);
  });

  it("finds the seller's own article regardless of case", () => {
    expect(matchesQuery(catalog[3], "ШУРУПОВЕРТ")).toBe(true);
    expect(matchesQuery(catalog[1], "пылесос")).toBe(true);
  });

  it("finds goods by their subject", () => {
    expect(matchesQuery(catalog[2], "насосы")).toBe(true);
    expect(matchesQuery(catalog[0], "перфор")).toBe(true);
  });

  it("survives the padding that comes with a pasted value", () => {
    expect(matchesQuery(catalog[0], "  1272526845 ")).toBe(true);
    expect(matchesQuery(catalog[3], "шуруповерт  22")).toBe(false);
    expect(matchesQuery(catalog[0], "   ")).toBe(true);
  });

  it("keeps everything when nothing is typed", () => {
    expect(catalog.every((entry) => matchesQuery(entry, ""))).toBe(true);
  });
});

describe("subjects", () => {
  it("labels goods that never came from the catalog", () => {
    expect(subjectOf(catalog[4])).toBe(NO_SUBJECT);
    expect(subjectOf(item("1", "a", "   "))).toBe(NO_SUBJECT);
  });

  it("counts what is present, most populated first", () => {
    const subjects = collectSubjects([...catalog, item("2", "b", "Перфораторы")]);
    expect(subjects[0]).toEqual({ name: "Перфораторы", count: 2 });
    expect(subjects.map((subject) => subject.name)).toContain(NO_SUBJECT);
    expect(subjects).toHaveLength(5);
  });

  it("orders equally populated subjects alphabetically", () => {
    const subjects = collectSubjects([item("1", "a", "Ящики"), item("2", "b", "Аптечки")]);
    expect(subjects.map((subject) => subject.name)).toEqual(["Аптечки", "Ящики"]);
  });
});

describe("applyFilters", () => {
  it("keeps everything when no filter is set", () => {
    expect(applyFilters(catalog, { query: "", subjects: [] })).toHaveLength(5);
  });

  it("narrows to the chosen subjects", () => {
    const result = applyFilters(catalog, { query: "", subjects: ["Перфораторы", "Пылесосы"] });
    expect(result.map((entry) => entry.article)).toEqual(["1272526845", "1261107992"]);
  });

  it("selects goods with no subject as a bucket of their own", () => {
    const result = applyFilters(catalog, { query: "", subjects: [NO_SUBJECT] });
    expect(result.map((entry) => entry.article)).toEqual(["1272524122"]);
  });

  it("applies the search on top of the subject filter", () => {
    expect(applyFilters(catalog, { query: "спутник", subjects: ["Перфораторы"] })).toHaveLength(1);
    expect(applyFilters(catalog, { query: "спутник", subjects: ["Пылесосы"] })).toHaveLength(0);
  });
});

describe("hasActiveFilters", () => {
  it("ignores whitespace typed into the search box", () => {
    expect(hasActiveFilters({ query: "   ", subjects: [] })).toBe(false);
    expect(hasActiveFilters({ query: "перф", subjects: [] })).toBe(true);
    expect(hasActiveFilters({ query: "", subjects: ["Пылесосы"] })).toBe(true);
  });
});
