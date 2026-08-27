import { describe, expect, it } from "vitest";

import { cargoLabel, officesLabel, warehouseState } from "./cargo";

describe("cargoLabel", () => {
  it("names the types WB actually returns", () => {
    expect(cargoLabel(1)).toBe("обычный");
    expect(cargoLabel(3)).toBe("КГТ");
  });

  it("shows an unknown type instead of pretending it is ordinary", () => {
    expect(cargoLabel(7)).toBe("тип 7");
  });
});

describe("warehouseState", () => {
  it("puts an unfinished warehouse apart from a working one", () => {
    expect(warehouseState({ is_deleting: false, is_processing: false })).toBe("готов");
    expect(warehouseState({ is_deleting: false, is_processing: true })).toBe("создаётся");
  });

  it("reports deletion even while WB is still processing it", () => {
    expect(warehouseState({ is_deleting: true, is_processing: true })).toBe("удаляется");
  });
});

describe("officesLabel", () => {
  it("склоняет по-русски", () => {
    expect(officesLabel(1)).toBe("1 объект");
    expect(officesLabel(2)).toBe("2 объекта");
    expect(officesLabel(5)).toBe("5 объектов");
    expect(officesLabel(11)).toBe("11 объектов");
    expect(officesLabel(21)).toBe("21 объект");
    expect(officesLabel(180)).toBe("180 объектов");
  });
});
