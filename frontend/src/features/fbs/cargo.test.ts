import { describe, expect, it } from "vitest";

import { cargoLabel, warehouseState } from "./cargo";

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
