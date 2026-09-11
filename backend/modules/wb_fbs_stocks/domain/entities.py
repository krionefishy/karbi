import uuid
from dataclasses import dataclass
from datetime import datetime

# Роли групп столбцов. Порядок в таблице задаёт селлер, роль нужна листу
# «Сравнение»: там «наш склад» раскрыт по складам, а округа стоят суммами.
GROUP_OWN = "own"
GROUP_FULFILMENT = "fulfilment"
GROUP_DISTRICT = "district"
GROUP_KINDS = (GROUP_OWN, GROUP_FULFILMENT, GROUP_DISTRICT)

# Склад продавца, куда FBS-заказ везёт сам продавец. DBS и EDBS — другие
# схемы доставки, их в таблице остатков селлер не смотрит.
DELIVERY_FBS = 1


@dataclass(frozen=True, slots=True)
class SellerWarehouse:
    """Склад продавца в кабинете WB, как его отдал `/api/v3/warehouses`."""

    warehouse_id: int
    office_id: int
    name: str
    delivery_type: int
    is_deleting: bool

    @property
    def fbs(self) -> bool:
        return self.delivery_type == DELIVERY_FBS


@dataclass(frozen=True, slots=True)
class ColumnGroup:
    """Группа столбцов: сводный столбец с суммой и склады под ним."""

    id: uuid.UUID
    title: str
    kind: str
    position: int


@dataclass(frozen=True, slots=True)
class Column:
    """Склад, показанный в таблице внутри своей группы."""

    warehouse_id: int
    group_id: uuid.UUID
    position: int


@dataclass(frozen=True, slots=True)
class BoardBarcode:
    """Строка таблицы. Список ведёт селлер руками: добавила — остаток подтянулся."""

    barcode: str
    note: str
    position: int
    added_at: datetime


@dataclass(frozen=True, slots=True)
class StockFact:
    """Остаток одного баркода на одном складе на момент сбора.

    Ноль хранится строкой: WB строку без остатка не отдаёт, и без явного нуля
    «остатка нет» и «склад не спрашивали» были бы неотличимы.
    """

    warehouse_id: int
    barcode: str
    amount: int
