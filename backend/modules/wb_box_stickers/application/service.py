"""Сопоставить Excel коробовки, PDF стикеров и упаковку поставки в WB.

Порядок берётся из Excel, страницы — из PDF, содержимое коробов — из WB.
Любое расхождение — проблема, при которой PDF не собирается: стикер,
наклеенный не на тот короб, хуже, чем стикеры, которые пришлось перепроверить.
"""

import asyncio
import re
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.modules.wb_box_stickers.application.stickers import build_stickers, read_stickers, verify_stickers
from backend.modules.wb_box_stickers.application.workbook import read_workbook
from backend.modules.wb_box_stickers.domain import (
    BoxLine,
    ItemLabel,
    MatchedBox,
    Package,
    StickerInputError,
    StickerPage,
    WorkbookBox,
)
from backend.modules.wb_box_stickers.infrastructure.wb import WBSuppliesClient
from backend.modules.wb_core.domain import EGRESS_SERVABLE, Seller
from backend.modules.wb_core.infrastructure.postgres import SellerRepository

# Сколько имён коробов показывать в одной проблеме: дальше список никто не читает.
NAMES_LIMIT = 10


class StickerBuildError(Exception):
    """Сопоставление с проблемами или собранный файл не прошёл проверку."""

    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)


@dataclass(frozen=True, slots=True)
class StickerPlan:
    supply_id: int | None
    seller_id: uuid.UUID | None
    seller_name: str
    boxes: list[MatchedBox]
    problems: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.problems and bool(self.boxes)


def _names(values: Sequence[str]) -> str:
    shown = ", ".join(values[:NAMES_LIMIT])
    return shown + (f" и ещё {len(values) - NAMES_LIMIT}" if len(values) > NAMES_LIMIT else "")


def _short(shk: str) -> str:
    return f"{shk[:3]} {shk[3:]}" if len(shk) == 7 else shk


def _same_name(left: str, right: str) -> bool:
    """«ИП Мунаева М. Л.» на стикере и «ИП Мунаева М.Л.» в реестре — один кабинет."""

    def norm(value: str) -> str:
        return re.sub(r"[\s.«»\"']", "", value).lower()

    return bool(left and right) and norm(left) == norm(right)


class BoxStickerService:
    def __init__(self, sellers: SellerRepository, supplies: WBSuppliesClient) -> None:
        self.sellers = sellers
        self.supplies = supplies

    async def plan(self, workbook: bytes, stickers: bytes) -> StickerPlan:
        boxes = await asyncio.to_thread(read_workbook, workbook)
        pages = await asyncio.to_thread(read_stickers, stickers)
        return await self._match(boxes, pages)

    async def build(self, workbook: bytes, stickers: bytes) -> tuple[StickerPlan, bytes]:
        plan = await self.plan(workbook, stickers)
        if not plan.ready:
            raise StickerBuildError(plan.problems or ["Нечего собирать"])
        data = await asyncio.to_thread(build_stickers, stickers, plan.boxes)
        problems = await asyncio.to_thread(verify_stickers, data, plan.boxes)
        if problems:
            raise StickerBuildError(["Собранный файл не прошёл проверку, отдавать его нельзя", *problems])
        return plan, data

    async def _match(self, boxes: list[WorkbookBox], pages: list[StickerPage]) -> StickerPlan:
        problems: list[str] = []
        supply_ids = {page.supply_id for page in pages if page.supply_id}
        if len(supply_ids) > 1:
            problems.append(f"В PDF стикеры разных поставок: {_names([str(item) for item in sorted(supply_ids)])}")
        supply_id = next(iter(supply_ids)) if len(supply_ids) == 1 else None
        if not supply_ids:
            problems.append("На стикерах не найден номер поставки — это не PDF стикеров коробов из кабинета WB")

        unread = [str(page.index + 1) for page in pages if not page.package_code]
        if unread:
            problems.append(f"Не читается QR на страницах PDF: {_names(unread)}")
        by_code: dict[str, StickerPage] = {}
        twice: list[str] = []
        for page in pages:
            if not page.package_code:
                continue
            if page.package_code in by_code:
                twice.append(_short(page.shk) or str(page.index + 1))
            by_code.setdefault(page.package_code, page)
        if twice:
            problems.append(f"Один и тот же короб в PDF дважды: {_names(twice)}")
        by_shk = {page.shk: page for page in pages if page.shk}

        # Страница короба: по полному коду из Excel, а без колонки кода — по номеру на стикере.
        placed: list[tuple[WorkbookBox, StickerPage]] = []
        missing: list[str] = []
        for box in boxes:
            found = by_code.get(box.package_code) if box.package_code else by_shk.get(box.shk)
            if found is None:
                missing.append(_short(box.shk))
                continue
            if found.shk and found.shk != box.shk:
                problems.append(
                    f"Короб {_short(box.shk)}: в Excel его код для печати стоит на стикере {_short(found.shk)}"
                )
                continue
            placed.append((box, found))
        if missing:
            problems.append(f"Нет стикеров в PDF для коробов из Excel: {_names(missing)}")
        used = {page.index for _, page in placed}
        extra = [_short(page.shk) or f"стр. {page.index + 1}" for page in pages if page.index not in used]
        if extra and not missing:
            problems.append(f"В PDF есть стикеры коробов, которых нет в Excel: {_names(extra)}")
        elif extra:
            problems.append(f"В PDF стикеры, не найденные в Excel: {_names(extra)} — Excel и PDF от одной поставки?")

        seller = await self._owner(supply_id, pages) if supply_id else None
        if supply_id and seller is None:
            problems.append(
                f"Поставка {supply_id} не нашлась ни в одном кабинете. Либо она чужая, "
                "либо у ключа нужного кабинета нет категории «Поставки»"
            )
        if seller is None or supply_id is None:
            return StickerPlan(
                supply_id=supply_id,
                seller_id=seller.id if seller else None,
                seller_name=seller.name if seller else "",
                boxes=self._boxes(placed, {}, {}),
                problems=problems,
            )

        packages = {package.code: package for package in await self.supplies.packages(str(seller.id), supply_id)}
        if not packages:
            problems.append(f"WB не отдаёт упаковку поставки {supply_id}: коробовку ещё не загрузили в кабинет?")
        else:
            problems.extend(self._check_contents(placed, packages))
        cards = await self.sellers.list_barcode_cards(seller.id)
        labels = {barcode: (card.vendor_code, card.tech_size) for barcode, card in cards.items()}
        return StickerPlan(
            supply_id=supply_id,
            seller_id=seller.id,
            seller_name=seller.name,
            boxes=self._boxes(placed, packages, labels),
            problems=problems,
        )

    async def _owner(self, supply_id: int, pages: list[StickerPage]) -> Seller | None:
        """Кабинет поставки: сначала тот, чьё имя напечатано на стикере, потом остальные."""
        printed = next((page.seller_name for page in pages if page.seller_name), "")
        sellers = [
            seller
            for seller in await self.sellers.list_sellers()
            if seller.egress_status in EGRESS_SERVABLE and not seller.is_archived
        ]
        sellers.sort(key=lambda seller: not _same_name(seller.name, printed))
        for seller in sellers:
            if await self.supplies.owns(str(seller.id), supply_id):
                return seller
        return None

    def _check_contents(self, placed: list[tuple[WorkbookBox, StickerPage]], packages: dict[str, Package]) -> list[str]:
        problems: list[str] = []
        unknown: list[str] = []
        for box, page in placed:
            package = packages.get(page.package_code)
            if package is None:
                unknown.append(_short(box.shk))
                continue
            if page.quantity is not None and page.quantity != package.quantity:
                problems.append(
                    f"Короб {_short(box.shk)}: на стикере {page.quantity} шт, а в WB {package.quantity} шт — "
                    "скачайте стикеры заново"
                )
            if box.lines and _counts(box.lines) != _counts(package.lines):
                problems.append(
                    f"Короб {_short(box.shk)}: в Excel {_describe(box.lines)}, а в WB {_describe(package.lines)} — "
                    "коробовку меняли после загрузки в WB?"
                )
        if unknown:
            problems.append(f"WB не знает короба {_names(unknown)} в этой поставке")
        return problems

    @staticmethod
    def _boxes(
        placed: list[tuple[WorkbookBox, StickerPage]],
        packages: dict[str, Package],
        labels: dict[str, tuple[str, str]],
    ) -> list[MatchedBox]:
        result = []
        for position, (box, page) in enumerate(placed, start=1):
            package = packages.get(page.package_code)
            lines = package.lines if package else box.lines
            items = tuple(
                ItemLabel(
                    barcode=barcode,
                    quantity=quantity,
                    vendor_code=labels.get(barcode, ("", ""))[0],
                    tech_size=labels.get(barcode, ("", ""))[1],
                )
                for barcode, quantity in _counts(lines).items()
            )
            quantity = package.quantity if package else page.quantity or sum(item.quantity for item in items)
            result.append(
                MatchedBox(
                    position=position,
                    shk=box.shk,
                    package_code=page.package_code,
                    page_index=page.index,
                    quantity=quantity,
                    items=items,
                )
            )
        return result


def _counts(lines: Sequence[BoxLine]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for line in lines:
        counts[line.barcode] += line.quantity
    return dict(counts)


def _describe(lines: Sequence[BoxLine]) -> str:
    return ", ".join(f"{barcode} × {quantity}" for barcode, quantity in _counts(lines).items())


__all__ = ["BoxStickerService", "StickerBuildError", "StickerInputError", "StickerPlan"]
