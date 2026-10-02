"""Себестоимость единицы товара по кабинету и артикулу, с датой начала действия."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

MARKETPLACE_WB = "wb"
MARKETPLACE_OZON = "ozon"
MARKETPLACES = (MARKETPLACE_WB, MARKETPLACE_OZON)


@dataclass(frozen=True, slots=True)
class CostPrice:
    """Себестоимость артикула в кабинете, действующая с `effective_from`.

    Ключ — кабинет и артикул: один и тот же артикул WB встречается у двух
    кабинетов с разной закупочной ценой. `article` — nmId у WB, SKU у Ozon.
    """

    seller_id: uuid.UUID
    marketplace: str
    article: str
    vendor_code: str
    cost: Decimal
    effective_from: date


class CostBook:
    """Себестоимость артикулов одного кабинета на дату.

    Новая цена действует со своей даты и прошлые периоды не меняет. Период
    раньше самой первой загрузки считается по ней же: иначе вся история до
    дня, когда файл впервые загрузили, осталась бы без себестоимости.
    """

    def __init__(self, prices: Iterable[CostPrice]) -> None:
        self._versions: dict[str, list[CostPrice]] = {}
        for price in prices:
            self._versions.setdefault(price.article, []).append(price)
        for versions in self._versions.values():
            versions.sort(key=lambda item: item.effective_from)

    def cost(self, article: str, on: date) -> Decimal | None:
        versions = self._versions.get(article)
        if not versions:
            return None
        current = versions[0]
        for version in versions:
            if version.effective_from > on:
                break
            current = version
        return current.cost

    def latest(self, article: str) -> CostPrice | None:
        versions = self._versions.get(article)
        return versions[-1] if versions else None
