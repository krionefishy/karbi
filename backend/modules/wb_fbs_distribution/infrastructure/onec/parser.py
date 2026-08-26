import csv
import io
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from backend.modules.wb_fbs_distribution.domain import StockLine, StockSnapshot

# Имена полей выгрузки. Приняты и русские, и латинские: пока формат 1С не
# согласован, отказываться от файла из-за заголовка — худшее, что можно сделать.
FIELDS: dict[str, tuple[str, ...]] = {
    "item_id": ("item_id", "id_1c", "id", "код", "номенклатура_id"),
    "barcode": ("barcode", "штрихкод", "баркод"),
    "name": ("name", "наименование", "название"),
    "characteristic": ("characteristic", "характеристика", "вариант", "размер"),
    "quantity": ("quantity", "остаток", "количество"),
}
GENERATED_AT = ("generated_at", "дата", "время", "сформировано")


class SnapshotFormatError(Exception):
    """Файл не разобрать: дело в форме данных, а не в их содержимом."""


def parse_snapshot(payload: bytes, *, received_at: datetime | None = None) -> StockSnapshot:
    """Разобрать выгрузку 1С из JSON или CSV.

    Формат определяется по первому непробельному символу, а не по имени файла:
    имя задаёт тот, кто выгружал, и оно врёт чаще, чем содержимое.
    """
    text = _decode(payload)
    stripped = text.lstrip()
    if not stripped:
        raise SnapshotFormatError("Файл пустой")
    rows, generated_at = _read_json(stripped) if stripped[0] in "[{" else _read_csv(text)
    if not rows:
        raise SnapshotFormatError("В выгрузке нет ни одной строки")
    lines = tuple(_line(row, index) for index, row in enumerate(rows, start=1))
    return StockSnapshot(generated_at=generated_at or (received_at or datetime.now(UTC)), lines=lines)


def _decode(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SnapshotFormatError("Кодировка файла не распознана: ожидается UTF-8 или Windows-1251")


def _read_json(text: str) -> tuple[list[dict], datetime | None]:
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise SnapshotFormatError(f"Не разобрать JSON: {error}") from error
    if isinstance(payload, list):
        return _dicts(payload), None
    if not isinstance(payload, dict):
        raise SnapshotFormatError("Ожидался список строк или объект со списком")
    rows = payload.get("lines") or payload.get("items") or payload.get("rows") or payload.get("остатки")
    if not isinstance(rows, list):
        raise SnapshotFormatError("В объекте нет списка строк (lines, items, rows)")
    return _dicts(rows), _moment(_pick(payload, GENERATED_AT))


def _read_csv(text: str) -> tuple[list[dict], datetime | None]:
    sample = text[:4096]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        # Один столбец или необычный разделитель: точка с запятой — то, что
        # выгружает 1С по умолчанию, и она же в согласованном формате.
        dialect = csv.excel
        dialect.delimiter = ";"
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = _dicts(list(reader))
    generated = _moment(_pick(rows[0], GENERATED_AT)) if rows else None
    return rows, generated


def _dicts(rows: Iterable[Any]) -> list[dict]:
    collected = [row for row in rows if isinstance(row, dict)]
    if not collected:
        raise SnapshotFormatError("Строки выгрузки не разобраны")
    return collected


def _pick(row: dict, names: tuple[str, ...]) -> Any:
    lowered = {str(key).strip().lower(): value for key, value in row.items() if key is not None}
    for name in names:
        if name in lowered and lowered[name] not in (None, ""):
            return lowered[name]
    return None


def _line(row: dict, index: int) -> StockLine:
    item_id = _pick(row, FIELDS["item_id"])
    barcode = _pick(row, FIELDS["barcode"])
    quantity = _pick(row, FIELDS["quantity"])
    if item_id is None and barcode is None:
        raise SnapshotFormatError(f"Строка {index}: нет ни идентификатора номенклатуры, ни баркода")
    if quantity is None:
        raise SnapshotFormatError(f"Строка {index}: нет остатка")
    return StockLine(
        # Если 1С не отдаёт свой идентификатор, ключом становится баркод: это
        # хуже, но честнее выдуманного номера строки.
        item_id=str(item_id if item_id is not None else barcode).strip(),
        barcode=str(barcode or "").strip(),
        name=str(_pick(row, FIELDS["name"]) or "").strip(),
        characteristic=str(_pick(row, FIELDS["characteristic"]) or "").strip(),
        quantity=_quantity(quantity, index),
    )


def _quantity(value: Any, index: int) -> int:
    if isinstance(value, bool):
        raise SnapshotFormatError(f"Строка {index}: остаток должен быть числом")
    if isinstance(value, int):
        return value
    text = str(value).strip().replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        number = float(text)
    except ValueError as error:
        raise SnapshotFormatError(f"Строка {index}: остаток «{value}» не число") from error
    if number != int(number):
        raise SnapshotFormatError(f"Строка {index}: дробный остаток «{value}» на складе не разложить")
    return int(number)


def _moment(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as error:
        raise SnapshotFormatError(f"Время формирования «{value}» не разобрать") from error
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
