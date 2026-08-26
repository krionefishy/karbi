/** Значения WB: 1 — обычный груз, 2 — СГТ, 3 — КГТ. */
const CARGO_LABELS: Record<number, string> = { 1: "обычный", 2: "СГТ", 3: "КГТ" };

export function cargoLabel(cargoType: number) {
  return CARGO_LABELS[cargoType] ?? `тип ${cargoType}`;
}

/** Что показать вместо статуса, пока WB обрабатывает создание или удаление. */
export function warehouseState(warehouse: { is_deleting: boolean; is_processing: boolean }) {
  if (warehouse.is_deleting) return "удаляется";
  if (warehouse.is_processing) return "создаётся";
  return "готов";
}
