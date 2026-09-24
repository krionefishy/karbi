import asyncio
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import RemainsMirror
from backend.modules.wb_core.domain import Seller
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_podsort.application.calculation import build_rows, place_warehouse, warehouse_stock
from backend.modules.wb_podsort.application.report import (
    PodsortReportFile,
    PodsortWorkbook,
    ReportContext,
    SellerSection,
)
from backend.modules.wb_podsort.application.view import (
    PodsortOverview,
    PodsortView,
    SellerState,
    SummaryRow,
    WarehouseView,
)
from backend.modules.wb_podsort.domain import (
    MAX_COVER_DAYS,
    TARGET_REGIONS,
    WINDOW_CHOICES,
    BarcodeInfo,
    Periods,
    PodsortSettings,
)
from backend.modules.wb_podsort.infrastructure.postgres import PodsortRepository


class PodsortQueryError(Exception):
    """Параметры, по которым считать нельзя: окно не из списка, регион, которого нет."""


@dataclass(frozen=True, slots=True)
class _Computed:
    periods: Periods
    settings: PodsortSettings
    sections: tuple[SellerSection, ...]
    states: tuple[SellerState, ...]
    warehouses: tuple[WarehouseView, ...]


class PodsortService:
    """Что интерфейс просит у подсорта: расчёт по всем подключённым кабинетам, настройки, выгрузка.

    Расчёт — при чтении, из суточных итогов заказов и зеркала остатков: окно,
    срок покрытия и регионы складов можно менять, и книга сразу считается
    по-новому, без пересборки чего-либо.
    """

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        podsort: PodsortRepository,
        *,
        timezone: ZoneInfo,
        history_days: int,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.podsort = podsort
        self.remains = RemainsMirror(session)
        self.timezone = timezone
        self.history_days = history_days

    async def overview(self) -> PodsortOverview:
        enrolled = {seller.id for seller in await self._enrolled()}
        last_success_at, failing = await self.podsort.collection_summary(enrolled)
        return PodsortOverview(seller_count=len(enrolled), last_success_at=last_success_at, failing=failing)

    # --- reading ---------------------------------------------------------------

    async def view(self, region: str | None = None, *, now: datetime | None = None) -> PodsortView:
        computed = await self._compute(now)
        chosen = region or (computed.settings.regions[0] if computed.settings.regions else TARGET_REGIONS[0])
        if chosen not in computed.settings.regions:
            raise PodsortQueryError(f"Регион «{chosen}» не выбран в настройках подсорта")
        settings = computed.settings
        rows = [
            SummaryRow(section.name, row, need)
            for section in computed.sections
            for row in section.rows
            if (need := row.targets[chosen].need(settings.window_days, settings.cover_days)) > 0
        ]
        return PodsortView(
            today=computed.periods.today,
            last_day=computed.periods.last_day,
            window_start=computed.periods.window_start(settings.window_days),
            settings=settings,
            sellers=computed.states,
            warehouses=computed.warehouses,
            region=chosen,
            rows=tuple(rows),
        )

    async def export(self, *, now: datetime | None = None) -> PodsortReportFile:
        computed = await self._compute(now)
        periods, settings = computed.periods, computed.settings
        context = ReportContext(
            today=periods.today,
            last_day=periods.last_day,
            window_start=periods.window_start(settings.window_days),
            months=periods.months,
            settings=settings,
            unplaced_warehouses=tuple(
                warehouse.name
                for warehouse in computed.warehouses
                if warehouse.source == "none" and warehouse.quantity > 0
            ),
        )
        content = await asyncio.to_thread(PodsortWorkbook(context, computed.sections).build)
        return PodsortReportFile(day=periods.today, content=content)

    # --- settings ---------------------------------------------------------------

    async def settings(self) -> PodsortSettings:
        return await self.podsort.settings()

    async def update_settings(
        self,
        *,
        window_days: int,
        cover_days: int,
        regions: Sequence[str],
        updated_by: uuid.UUID | None = None,
    ) -> PodsortSettings:
        if window_days not in WINDOW_CHOICES:
            raise PodsortQueryError(f"Окно продаж — {' или '.join(map(str, WINDOW_CHOICES))} дней")
        if not 1 <= cover_days <= MAX_COVER_DAYS:
            raise PodsortQueryError(f"Дней покрытия — от 1 до {MAX_COVER_DAYS}")
        chosen = tuple(dict.fromkeys(region for region in regions if region))
        unknown = [region for region in chosen if region not in TARGET_REGIONS]
        if unknown:
            raise PodsortQueryError(f"Нет такого региона: {', '.join(unknown)}")
        if not chosen:
            raise PodsortQueryError("Выберите хотя бы один регион")
        settings = PodsortSettings(window_days=window_days, cover_days=cover_days, regions=chosen)
        await self.podsort.save_settings(settings, updated_by=updated_by)
        await self.session.commit()
        return settings

    async def set_warehouse_region(self, name: str, region: str | None, *, guess: bool = False) -> None:
        """Регион склада WB: задать, снять («не относится ни к какому») или вернуть угадывание по городу."""
        name = name.strip()
        if not name:
            raise PodsortQueryError("Не указан склад")
        if guess:
            await self.podsort.reset_warehouse_region(name)
        else:
            if region is not None and region not in TARGET_REGIONS:
                raise PodsortQueryError(f"Нет такого региона: {region}")
            await self.podsort.set_warehouse_region(name, region)
        await self.session.commit()

    # --- расчёт -----------------------------------------------------------------

    async def _compute(self, now: datetime | None) -> _Computed:
        stamp = now or datetime.now(UTC)
        periods = Periods(stamp.astimezone(self.timezone).date())
        settings = await self.podsort.settings()
        overrides = await self.podsort.warehouse_overrides()
        tracked = {row.seller_id: row for row in await self.podsort.tracked_rows()}
        window_start = periods.window_start(settings.window_days)
        month_ranges = [(month, periods.month_end(month)) for month in periods.months]
        history_start = periods.today - timedelta(days=self.history_days)

        sections: list[SellerSection] = []
        states: list[SellerState] = []
        stock_by_warehouse: dict[str, int] = defaultdict(int)
        for seller in sorted(await self._enrolled(), key=lambda item: item.name.lower()):
            loaded = await self.podsort.loaded_days(seller.id, history_start)
            window_loaded = sum(1 for day in loaded if window_start <= day <= periods.last_day)
            totals = await self.podsort.totals(
                seller.id, months=month_ranges, last_day=periods.last_day, window_start=window_start
            )
            snapshot = await self.remains.snapshot(seller.id)
            rows = build_rows(
                seller.name,
                totals,
                snapshot.remains,
                await self._infos(seller.id),
                overrides,
                settings,
                months=len(month_ranges),
            )
            for name, quantity in warehouse_stock(snapshot.remains).items():
                stock_by_warehouse[name] += quantity
            sections.append(SellerSection(seller.name, rows, window_loaded, snapshot.collected_at))
            row = tracked[seller.id]
            states.append(
                SellerState(
                    seller_id=seller.id,
                    name=seller.name,
                    window_days_loaded=window_loaded,
                    history_from=min(loaded) if loaded else None,
                    history_days_loaded=len(loaded),
                    collected_at=row.collected_at,
                    collection_error=row.collection_error,
                    remains_at=snapshot.collected_at,
                    remains_error=snapshot.error,
                )
            )
        warehouses = []
        for name, quantity in stock_by_warehouse.items():
            place = place_warehouse(name, overrides)
            warehouses.append(WarehouseView(name, place.region, place.source, quantity))
        # Сначала то, что требует решения человека: склады, про которые не знаем ничего.
        warehouses.sort(key=lambda item: (item.source != "none", item.region or "", -item.quantity, item.name))
        return _Computed(periods, settings, tuple(sections), tuple(states), tuple(warehouses))

    async def _infos(self, seller_id: uuid.UUID) -> dict[str, BarcodeInfo]:
        """Подпись баркода: каталог главнее — там артикул продавца сейчас; из заказов — то, чего в каталоге нет."""
        infos = await self.podsort.barcodes(seller_id)
        for barcode, card in (await self.sellers.list_barcode_cards(seller_id)).items():
            known = infos.get(barcode)
            infos[barcode] = BarcodeInfo(
                barcode=barcode,
                nm_id=int(card.article) if card.article.isdigit() else (known.nm_id if known else 0),
                vendor_code=card.vendor_code or (known.vendor_code if known else ""),
                subject=card.subject_name or (known.subject if known else ""),
                tech_size=card.tech_size or (known.tech_size if known else ""),
            )
        return infos

    async def _enrolled(self) -> list[Seller]:
        tracked = await self.podsort.tracked_seller_ids()
        return [seller for seller in await self.sellers.list_sellers() if seller.id in tracked]
