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

/** «1 объект, 2 объекта, 5 объектов» — счётчик в шапке справочника. */
export function officesLabel(count: number) {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 >= 11 && mod100 <= 14) return `${count} объектов`;
  if (mod10 === 1) return `${count} объект`;
  if (mod10 >= 2 && mod10 <= 4) return `${count} объекта`;
  return `${count} объектов`;
}
