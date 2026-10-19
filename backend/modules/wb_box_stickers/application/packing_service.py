import asyncio
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime

from backend.modules.wb_box_stickers.application.assembly import parse_assembly
from backend.modules.wb_box_stickers.application.packing import PackingPlan, pack
from backend.modules.wb_box_stickers.application.service import StickerBuildError, find_owner
from backend.modules.wb_box_stickers.domain import Assembly, StoredPacking, SupplyGood
from backend.modules.wb_box_stickers.infrastructure.redis import PackingStore
from backend.modules.wb_box_stickers.infrastructure.wb import WBSuppliesClient
from backend.modules.wb_core.infrastructure.postgres import SellerRepository

NAMES_LIMIT = 10


def _names(values: Sequence[str]) -> str:
    shown = ", ".join(values[:NAMES_LIMIT])
    return shown + (f" и ещё {len(values) - NAMES_LIMIT}" if len(values) > NAMES_LIMIT else "")


def check_against_goods(assembly: Assembly, goods: Sequence[SupplyGood]) -> list[str]:
    """Строки сборки против заявленного в поставке: баркоды и количества должны совпасть."""
    declared = {good.barcode: good.quantity for good in goods}
    packed: Counter[str] = Counter()
    for row in assembly.rows:
        packed[row.barcode] += sum(row.boxes)
    problems: list[str] = []
    unknown = [barcode for barcode in packed if barcode not in declared]
    if unknown:
        problems.append(f"Этих баркодов нет в поставке в WB: {_names(unknown)} — не те строки или не та поставка?")
    differs = [
        f"{barcode}: по коробам {packed[barcode]} шт, в поставке заявлено {declared[barcode]} шт"
        for barcode in packed
        if barcode in declared and packed[barcode] != declared[barcode]
    ]
    if differs:
        problems.append("Количество по коробам не совпадает с заявленным в поставке: " + _names(differs))
    missing = [barcode for barcode in declared if barcode not in packed]
    if missing:
        problems.append(f"В поставке в WB заявлены товары, которых нет в строках сборки: {_names(missing)}")
    return problems


class PackingService:
    def __init__(self, sellers: SellerRepository, supplies: WBSuppliesClient, store: PackingStore) -> None:
        self.sellers = sellers
        self.supplies = supplies
        self.store = store

    async def preview(self, template: bytes, text: str) -> PackingPlan:
        plan, _ = await self._pack(template, text)
        return plan

    async def build(self, template: bytes, text: str) -> tuple[PackingPlan, bytes]:
        plan, data = await self._pack(template, text)
        if not plan.ready or data is None or plan.supply_id is None or plan.seller_id is None:
            raise StickerBuildError(plan.problems or ["Нечего раскладывать"])
        await self.store.save(
            StoredPacking(
                supply_id=plan.supply_id,
                seller_id=plan.seller_id,
                seller_name=plan.seller_name,
                boxes=len(plan.boxes),
                workbook=data,
                saved_at=datetime.now(UTC),
            )
        )
        return plan, data

    async def recent(self) -> list[StoredPacking]:
        return await self.store.recent()

    async def _pack(self, template: bytes, text: str) -> tuple[PackingPlan, bytes | None]:
        assembly = parse_assembly(text)
        plan, data = await asyncio.to_thread(pack, template, assembly)
        problems = list(plan.problems)
        seller = await find_owner(self.sellers, self.supplies, assembly.supply_id) if assembly.supply_id else None
        if assembly.supply_id and seller is None:
            problems.append(
                f"Поставка {assembly.supply_id} не нашлась ни в одном кабинете. Либо она чужая, "
                "либо у ключа нужного кабинета нет категории «Поставки»"
            )
        if seller is not None and assembly.rows:
            goods = await self.supplies.goods(str(seller.id), assembly.supply_id or 0)
            if not goods:
                problems.append(f"WB не отдаёт товары поставки {assembly.supply_id}: в ней ещё нет товаров?")
            else:
                problems.extend(check_against_goods(assembly, goods))
        ready_plan = PackingPlan(
            boxes=plan.boxes if not problems else [],
            problems=problems,
            supply_id=assembly.supply_id,
            seller_id=str(seller.id) if seller else None,
            seller_name=seller.name if seller else "",
        )
        return ready_plan, (data if not problems else None)
