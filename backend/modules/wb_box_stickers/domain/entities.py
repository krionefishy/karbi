from dataclasses import dataclass
from datetime import datetime


class StickerInputError(Exception):
    """Файл не тот или не читается — повтор с тем же файлом не поможет."""


@dataclass(frozen=True, slots=True)
class BoxLine:
    barcode: str
    quantity: int


@dataclass(frozen=True, slots=True)
class WorkbookBox:
    """Короб из Excel коробовки: порядок строк — порядок, в котором нужны стикеры."""

    shk: str
    package_code: str
    lines: tuple[BoxLine, ...]
    row: int


@dataclass(frozen=True, slots=True)
class StickerPage:
    """Страница PDF со стикером: что написано текстом и что зашито в QR."""

    index: int
    shk: str
    package_code: str
    quantity: int | None
    supply_id: int | None
    seller_name: str


@dataclass(frozen=True, slots=True)
class Package:
    """Короб по данным WB: полный код и содержимое."""

    code: str
    quantity: int
    lines: tuple[BoxLine, ...]


@dataclass(frozen=True, slots=True)
class ItemLabel:
    barcode: str
    quantity: int
    vendor_code: str
    tech_size: str


@dataclass(frozen=True, slots=True)
class MatchedBox:
    """Короб на своём месте в новом PDF: откуда страница и что допечатать."""

    position: int
    shk: str
    package_code: str
    page_index: int
    quantity: int
    items: tuple[ItemLabel, ...]


@dataclass(frozen=True, slots=True)
class AssemblyRow:
    """Строка таблицы сборки склада: один товар поставки и как он разложен по коробам.

    Схема «3-60+1-40» — три короба по 60 и один на 40; `boxes` — она же, развёрнутая
    в количество на каждый короб по порядку.
    """

    line: int
    supply_id: int | None
    barcode: str
    quantity: int | None
    scheme: str
    boxes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Assembly:
    """Разобранная вставка из таблицы сборки: поставка, строки по порядку, что не сошлось."""

    supply_id: int | None
    rows: tuple[AssemblyRow, ...]
    problems: tuple[str, ...]

    @property
    def boxes(self) -> int:
        return sum(len(row.boxes) for row in self.rows)


@dataclass(frozen=True, slots=True)
class PackedBox:
    """Короб шаблона WB, получивший товар: позиция по таблице сборки и что в нём."""

    position: int
    shk: str
    package_code: str
    barcode: str
    quantity: int


@dataclass(frozen=True, slots=True)
class SupplyGood:
    """Товар поставки по данным WB: что заявлено к отгрузке и сколько."""

    barcode: str
    quantity: int
    vendor_code: str
    tech_size: str


@dataclass(frozen=True, slots=True)
class StoredPacking:
    """Коробовка, которую мы заполнили и отдали: помнится под номером поставки,
    чтобы на шаге стикеров Excel не загружали второй раз."""

    supply_id: int
    seller_id: str
    seller_name: str
    boxes: int
    workbook: bytes
    saved_at: datetime
