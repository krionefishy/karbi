import asyncio
import re
import uuid
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import OrderMirror, OrderTrace, SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository, SellerRepository
from backend.modules.wb_fbs_penalties.application.report import PenaltiesReportFile, WorkbookWriter
from backend.modules.wb_fbs_penalties.application.tracing import row_keys, row_trace
from backend.modules.wb_fbs_penalties.application.view import (
    GroupTotal,
    LookupMiss,
    LookupView,
    PenaltiesOverview,
    PenaltiesView,
    PenaltyRowView,
    RefreshRequest,
    WarehouseOption,
)
from backend.modules.wb_fbs_penalties.domain import GROUP_TITLES, GROUPS, ReportRow
from backend.modules.wb_fbs_penalties.infrastructure.postgres import PenaltiesRepository, RefreshRequestModel

DIGITS = re.compile(r"^\d{6,20}$")
SRID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class PenaltiesQueryError(Exception):
    """Запрос, по которому нечего показать: пустой список номеров, перевёрнутый период."""


class PenaltiesService:
    """Что интерфейс просит у штрафов: таблица за период, проверка номеров, выгрузка."""

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        penalties: PenaltiesRepository,
        *,
        timezone: ZoneInfo,
        orders_history_months: int,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.penalties = penalties
        self.orders = OrderMirror(session)
        self.mirror = MirrorRepository(session)
        self.timezone = timezone
        self.orders_history_months = orders_history_months

    async def overview(self) -> PenaltiesOverview:
        enrolled = await self._enrolled_ids()
        last_success_at, failing = await self.penalties.collection_summary(enrolled)
        return PenaltiesOverview(seller_count=len(enrolled), last_success_at=last_success_at, failing=failing)

    # --- reading ---------------------------------------------------------------

    async def view(
        self,
        seller_id: uuid.UUID,
        date_from: date,
        date_to: date,
        *,
        group: str | None = None,
        warehouse_id: int | None = None,
        page: int = 1,
        page_size: int | None = None,
    ) -> PenaltiesView:
        """Страница удержаний за период; `page_size=None` — всё сразу.

        Склад и поставка сведены при сборе и лежат на строке, поэтому страница,
        итоги и фильтры — запросы к одной таблице модуля.
        """
        if date_from > date_to:
            raise PenaltiesQueryError("Начало периода позже его конца")
        seller_name = await self._enrolled(seller_id)
        tracked = await self.penalties.tracked(seller_id)
        offset = (page - 1) * (page_size or 0)
        rows = await self.penalties.rows_in_period(
            seller_id, date_from, date_to, group=group, warehouse_id=warehouse_id, limit=page_size, offset=offset
        )
        total_rows = await self.penalties.count_in_period(
            seller_id, date_from, date_to, group=group, warehouse_id=warehouse_id
        )
        totals = await self._totals(seller_id, date_from, date_to, warehouse_id=warehouse_id)
        # Склады для фильтра — все, что есть в кабинете, плюс те, с которых в периоде
        # уходили заказы: так фильтр не прячет склад, у которого сегодня чисто.
        warehouses = {
            item.warehouse_id: item.name for item in (await self.mirror.seller_warehouses(seller_id)).values()
        }
        for warehouse, name in (await self.penalties.warehouses_in_period(seller_id, date_from, date_to)).items():
            warehouses.setdefault(warehouse, name)
        return PenaltiesView(
            seller_id=seller_id,
            seller_name=seller_name,
            date_from=date_from,
            date_to=date_to,
            collected_at=tracked.collected_at if tracked else None,
            collection_error=tracked.collection_error if tracked else None,
            rows=tuple(PenaltyRowView(row) for row in rows),
            totals=totals,
            warehouses=tuple(
                WarehouseOption(warehouse_id, name)
                for warehouse_id, name in sorted(warehouses.items(), key=lambda item: item[1].lower())
            ),
            page=page,
            page_size=page_size,
            total_rows=total_rows,
        )

    async def lookup(self, seller_id: uuid.UUID, raw_keys: Sequence[str]) -> LookupView:
        """Вставленные номера: стикер МП, номер сборочного задания или `srid`.

        Сначала ищем в строках отчёта — там и штраф, и все номера; чего в отчёте
        нет, ищем среди заданий зеркала — без суммы, но со складом и поставкой.
        """
        await self._enrolled(seller_id)
        keys = list(dict.fromkeys(key.strip() for key in raw_keys if key.strip()))
        if not keys:
            raise PenaltiesQueryError("Вставьте хотя бы один номер")
        numbers = [int(key) for key in keys if DIGITS.match(key)]
        srids = [key for key in keys if not DIGITS.match(key) and SRID.match(key)]
        found = await self.penalties.rows_by_keys(seller_id, srids=srids, assembly_ids=numbers, sticker_ids=numbers)
        views = [PenaltyRowView(row) for row in found]
        matched = {key for view in views for key in row_keys(view.row)}
        rest = [key for key in keys if key not in matched]

        misses: list[LookupMiss] = []
        if rest:
            rest_numbers = [int(key) for key in rest if DIGITS.match(key)]
            traces = await self.orders.resolve(
                seller_id,
                rids=[key for key in rest if not DIGITS.match(key)],
                order_ids=rest_numbers,
                sticker_ids=rest_numbers,
            )
            seen: set[int] = set()
            for key in rest:
                trace = traces.get(key)
                if trace is None:
                    misses.append(LookupMiss(key, self._miss_reason(key)))
                    continue
                if trace.order.order_id in seen:
                    continue
                seen.add(trace.order.order_id)
                views.append(self._from_trace(trace))
        return LookupView(rows=tuple(views), missing=tuple(misses))

    async def export(
        self,
        seller_id: uuid.UUID,
        date_from: date,
        date_to: date,
        *,
        group: str | None = None,
        warehouse_id: int | None = None,
    ) -> PenaltiesReportFile:
        """Книга с тем, что открыто на экране: период, вкладка группы, склад — все страницы разом.

        Строки идут из базы порциями и сразу пишутся в книгу: период целиком в
        памяти не держится ни в Python, ни в openpyxl.
        """
        if date_from > date_to:
            raise PenaltiesQueryError("Начало периода позже его конца")
        seller_name = await self._enrolled(seller_id)
        # Итоги сверху — по тому, что в файле, а не по всем группам периода.
        totals = await self._totals(seller_id, date_from, date_to, group=group, warehouse_id=warehouse_id)
        writer = WorkbookWriter(seller_name, date_from, date_to, totals)
        async for chunk in self.penalties.iter_rows_in_period(
            seller_id, date_from, date_to, group=group, warehouse_id=warehouse_id
        ):
            await asyncio.to_thread(writer.add, [PenaltyRowView(row) for row in chunk])
        content = await asyncio.to_thread(writer.finish)
        return PenaltiesReportFile(
            seller_name=seller_name, date_from=date_from, date_to=date_to, group=group, content=content
        )

    # --- refresh ----------------------------------------------------------------

    async def request_refresh(self, seller_id: uuid.UUID, requested_by: uuid.UUID | None = None) -> RefreshRequest:
        await self._enrolled(seller_id)
        request = await self.penalties.request_refresh(seller_id, requested_by)
        await self.session.commit()
        return self._refresh(request)

    async def refresh_state(self, seller_id: uuid.UUID) -> RefreshRequest | None:
        await self._enrolled(seller_id)
        request = await self.penalties.latest_refresh(seller_id)
        return self._refresh(request) if request else None

    # --- helpers ----------------------------------------------------------------------

    async def _totals(
        self,
        seller_id: uuid.UUID,
        date_from: date,
        date_to: date,
        *,
        group: str | None = None,
        warehouse_id: int | None = None,
    ) -> tuple[GroupTotal, ...]:
        by_group = await self.penalties.totals_in_period(
            seller_id, date_from, date_to, group=group, warehouse_id=warehouse_id
        )
        return tuple(GroupTotal(name, GROUP_TITLES[name], *by_group[name]) for name in GROUPS if name in by_group)

    @staticmethod
    def _from_trace(trace: OrderTrace) -> PenaltyRowView:
        """Задание без строки отчёта: штрафа по нему нет, есть склад и поставка."""
        return PenaltyRowView(replace(PenaltiesService._stub_row(trace), trace=row_trace(trace)))

    @staticmethod
    def _stub_row(trace: OrderTrace) -> ReportRow:
        """Задание без строки отчёта: штрафа по нему нет, есть только логистика."""
        order = trace.order
        day = order.created_at.date()
        return ReportRow(
            rrd_id=0,
            realizationreport_id=0,
            date_from=day,
            date_to=day,
            create_dt=None,
            srid=order.rid,
            assembly_id=order.order_id,
            sticker_id=order.sticker_id,
            order_dt=order.created_at,
            sale_dt=None,
            rr_dt=None,
            nm_id=order.nm_id,
            sa_name="",
            subject_name="",
            barcode=order.sku,
            ts_name="",
            bonus_type_name="",
            supplier_oper_name="",
            delivery_method="FBS",
            office_name="",
            penalty=0.0,
            deduction=0.0,
            rebill_logistic_cost=0.0,
            storage_fee=0.0,
            additional_payment=0.0,
            acceptance=0.0,
        )

    def _miss_reason(self, key: str) -> str:
        if DIGITS.match(key) or SRID.match(key):
            return f"нет ни в отчётах, ни среди заданий за {self.orders_history_months} мес.: проверьте кабинет и номер"
        return "не похоже ни на стикер, ни на номер задания, ни на srid"

    async def _enrolled_ids(self) -> set[uuid.UUID]:
        tracked = await self.penalties.tracked_seller_ids()
        active = {seller.id for seller in await self.sellers.list_sellers()}
        return tracked & active

    async def _enrolled(self, seller_id: uuid.UUID) -> str:
        seller = await self.sellers.get(seller_id)
        if seller is None or seller.archived_at is not None:
            raise SellerNotFoundError
        if await self.penalties.tracked(seller_id) is None:
            raise SellerNotFoundError
        return seller.name

    @staticmethod
    def _refresh(request: RefreshRequestModel) -> RefreshRequest:
        return RefreshRequest(
            status=request.status,
            requested_at=request.requested_at or datetime.now(UTC),
            finished_at=request.finished_at,
            error=request.error,
        )
