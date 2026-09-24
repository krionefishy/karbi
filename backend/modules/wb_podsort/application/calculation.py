from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from backend.modules.wb_core.domain import WarehouseRemain
from backend.modules.wb_podsort.domain import (
    BarcodeInfo,
    PodsortRow,
    PodsortSettings,
    RegionFigures,
    guess_warehouse_region,
    is_service_warehouse,
    is_unplaced_warehouse,
)
from backend.modules.wb_podsort.infrastructure.postgres import CountTotals


@dataclass(frozen=True, slots=True)
class WarehousePlace:
    """Куда отнесён склад WB и откуда это известно."""

    name: str
    region: str | None
    # manual — задал человек, guess — по городу в названии, unplaced — WB сам
    # не говорит, где остаток («Склад WB РФ»), none — не знаем, решает человек.
    source: str


def place_warehouse(name: str, overrides: Mapping[str, str | None]) -> WarehousePlace:
    if name in overrides:
        return WarehousePlace(name, overrides[name], "manual")
    if is_unplaced_warehouse(name):
        return WarehousePlace(name, None, "unplaced")
    region = guess_warehouse_region(name)
    return WarehousePlace(name, region, "guess" if region else "none")


def build_rows(
    seller_name: str,
    totals: Sequence[CountTotals],
    remains: Iterable[WarehouseRemain],
    infos: Mapping[str, BarcodeInfo],
    overrides: Mapping[str, str | None],
    settings: PodsortSettings,
    *,
    months: int,
) -> list[PodsortRow]:
    """Строки кабинета: баркод, заказы по периодам и регионам, остаток и подсорт по целевым регионам.

    Остаток — только склады WB, чей регион известен. «В пути» и итог отчёта не
    считаются: товар в дороге к покупателю на полке уже не лежит. «Склад WB
    РФ» и склады без региона собираются отдельно — это остаток, про который
    WB не говорит, где он, и вычитать его из конкретного региона нельзя.
    """
    rows: dict[str, PodsortRow] = {}

    def row_for(barcode: str) -> PodsortRow:
        row = rows.get(barcode)
        if row is None:
            info = infos.get(barcode) or BarcodeInfo(barcode, 0, "", "", "")
            row = PodsortRow(
                seller_name=seller_name,
                info=info,
                month_orders=[0] * months,
                targets={region: RegionFigures() for region in settings.regions},
            )
            rows[barcode] = row
        return row

    for total in totals:
        row = row_for(total.barcode)
        row.month_orders = [have + add for have, add in zip(row.month_orders, total.months, strict=True)]
        row.orders_14 += total.orders_14
        row.orders_7 += total.orders_7
        row.window_orders += total.window
        row.window_fbs_orders += total.window_fbs
        if total.month_current:
            row.month_by_region[total.region] = row.month_by_region.get(total.region, 0) + total.month_current
        target = row.targets.get(total.region)
        if target is not None:
            target.window_orders += total.window

    places: dict[str, WarehousePlace] = {}
    for remain in remains:
        if remain.quantity <= 0 or is_service_warehouse(remain.warehouse_name):
            continue
        place = places.get(remain.warehouse_name)
        if place is None:
            place = places[remain.warehouse_name] = place_warehouse(remain.warehouse_name, overrides)
        row = row_for(remain.barcode)
        if row.info.nm_id == 0:
            row.info = BarcodeInfo(remain.barcode, int(remain.article), remain.vendor_code, "", remain.tech_size)
        target = row.targets.get(place.region) if place.region else None
        if target is not None:
            target.stock += remain.quantity
        elif place.region is None:
            row.unplaced_stock += remain.quantity

    return sorted(rows.values(), key=lambda row: (-row.window_orders, -sum(row.month_orders), row.info.vendor_code))


def warehouse_stock(remains: Iterable[WarehouseRemain]) -> dict[str, int]:
    """Сколько штук лежит на каждом складе — для списка складов в интерфейсе."""
    stock: dict[str, int] = defaultdict(int)
    for remain in remains:
        if not is_service_warehouse(remain.warehouse_name):
            stock[remain.warehouse_name] += remain.quantity
    return dict(stock)
