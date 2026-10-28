import io
import re
from dataclasses import dataclass

from openpyxl import load_workbook

LIST_SHEET = "основа"
HEADERS = {"кабинет": "seller", "баркод": "barcode", "номенклатура 1с": "nomenclature"}


class BoardFileError(Exception):
    """Файл не похож на список товаров: нет листа или нужных столбцов."""


@dataclass(frozen=True, slots=True)
class SheetRow:
    seller: str
    barcode: str
    nomenclature: str


def normalize(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _barcode(value: object) -> str:
    if value is None:
        return ""
    return str(int(value)) if isinstance(value, float) else str(value).strip()


def parse_list(content: bytes) -> list[SheetRow]:
    """Строки листа «ОСНОВА»: кабинет, баркод и номенклатура 1С. Остальное берётся из каталога.

    Нет листа с таким именем — берётся первый: аналитик может прислать и выгрузку
    с другим названием листа, лишь бы столбцы назывались так же.
    """
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as error:
        raise BoardFileError("Файл не открылся как Excel (.xlsx)") from error
    sheet = next((item for item in workbook.worksheets if normalize(item.title) == LIST_SHEET), None)
    sheet = sheet or workbook.worksheets[0]
    rows = sheet.iter_rows(values_only=True)
    header = next(rows, None)
    if header is None:
        raise BoardFileError("Лист пустой")
    columns = {HEADERS[normalize(name)]: index for index, name in enumerate(header) if normalize(name) in HEADERS}
    missing = [name for name, key in HEADERS.items() if key not in columns]
    if missing:
        raise BoardFileError(f"На листе нет столбцов: {', '.join(missing)}")
    collected = []
    for row in rows:
        seller = str(row[columns["seller"]] or "").strip()
        barcode = _barcode(row[columns["barcode"]])
        if seller and barcode:
            collected.append(SheetRow(seller, barcode, str(row[columns["nomenclature"]] or "").strip()))
    return collected
