"""Стикеры коробов FBO-поставки и коробовка селлера — как их отдаёт WB.

У короба три имени. Короткий номер (`618 0474`) напечатан на стикере текстом и
стоит в колонке «ШК короба» Excel коробовки. Полный код (`$Ts;…;TAS`) зашит в QR
стикера, стоит в колонке «ШК короба для печати в стороннем сервисе» и приходит
из API поставок как `packageCode` вместе с содержимым короба. Сопоставление
держится на полном коде: он и сканируется на приёмке, и описывает содержимое.
"""

from dataclasses import dataclass


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
