import asyncio
import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_fbs_stocks.application.report import BoardReportFile, render_workbook
from backend.modules.wb_fbs_stocks.application.view import (
    BoardOverview,
    BoardView,
    ColumnView,
    GroupView,
    RefreshRequest,
    RowView,
    SetupView,
    WarehouseSetup,
)
from backend.modules.wb_fbs_stocks.domain import GROUP_KINDS
from backend.modules.wb_fbs_stocks.infrastructure.postgres import FbsStocksRepository, RefreshRequestModel

# Баркод WB — цифры, но чужие штрихкоды бывают с буквами; режем только мусор
# вокруг и то, что явно не баркод.
BARCODE = re.compile(r"^[0-9A-Za-z-]{4,64}$")


class BoardConflictError(Exception):
    """Команду выполнять нельзя: она сломала бы таблицу."""


class FbsStocksService:
    """Что интерфейс просит у таблицы: строки, группы, заметки, выгрузка."""

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        stocks: FbsStocksRepository,
        *,
        timezone: ZoneInfo,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.stocks = stocks
        self.timezone = timezone

    async def overview(self) -> BoardOverview:
        enrolled = await self._enrolled_ids()
        last_success_at, failing, earliest, uncollected = await self.stocks.collection_summary(enrolled)
        return BoardOverview(
            seller_count=len(enrolled),
            last_success_at=last_success_at,
            failing=failing,
            earliest_collected_at=earliest,
            uncollected=uncollected,
        )

    # --- reading ---------------------------------------------------------------

    async def view(self, seller_id: uuid.UUID) -> BoardView:
        seller_name = await self._enrolled(seller_id)
        tracked = await self.stocks.tracked(seller_id)
        groups = await self._groups(seller_id)
        by_barcode: dict[str, dict[int, int]] = {}
        for (warehouse_id, barcode), amount in (await self.stocks.facts(seller_id)).items():
            by_barcode.setdefault(barcode, {})[warehouse_id] = amount
        sizes = await self.sellers.list_barcode_sizes(seller_id)
        articles = {article.article: article for article in await self.sellers.list_articles(seller_id)}
        rows: list[RowView] = []
        for item in await self.stocks.barcodes(seller_id):
            known = sizes.get(item.barcode)
            article = articles.get(known[1]) if known else None
            rows.append(
                RowView(
                    barcode=item.barcode,
                    note=item.note,
                    article=known[1] if known else "",
                    title=article.name if article else "",
                    vendor_code=article.vendor_code if article else "",
                    in_catalog=known is not None,
                    amounts=by_barcode.get(item.barcode, {}),
                )
            )
        return BoardView(
            seller_id=seller_id,
            seller_name=seller_name,
            collected_at=tracked.collected_at if tracked else None,
            collection_error=tracked.collection_error if tracked else None,
            groups=groups,
            rows=tuple(rows),
        )

    async def views(self) -> list[BoardView]:
        """Таблицы всех подключённых кабинетов — для сравнения и выгрузки."""
        enrolled = await self._enrolled_ids()
        sellers = sorted(
            (seller for seller in await self.sellers.list_sellers() if seller.id in enrolled),
            key=lambda seller: seller.name.lower(),
        )
        return [await self.view(seller.id) for seller in sellers]

    async def setup(self, seller_id: uuid.UUID) -> SetupView:
        await self._enrolled(seller_id)
        groups = await self._groups(seller_id)
        placed = {column.warehouse_id: column for column in await self.stocks.columns(seller_id)}
        warehouses = tuple(
            WarehouseSetup(
                warehouse_id=warehouse.warehouse_id,
                name=warehouse.name,
                delivery_type=warehouse.delivery_type,
                is_deleting=warehouse.is_deleting,
                group_id=placed[warehouse.warehouse_id].group_id if warehouse.warehouse_id in placed else None,
                position=placed[warehouse.warehouse_id].position if warehouse.warehouse_id in placed else 0,
            )
            for warehouse in await self.stocks.warehouses(seller_id)
        )
        return SetupView(seller_id=seller_id, groups=groups, warehouses=warehouses)

    # --- groups and columns ------------------------------------------------------

    async def add_group(self, seller_id: uuid.UUID, title: str, kind: str) -> GroupView:
        await self._enrolled(seller_id)
        title, kind = self._group_fields(title, kind)
        group = await self.stocks.add_group(seller_id, title, kind)
        await self.session.commit()
        return GroupView(id=group.id, title=group.title, kind=group.kind, columns=())

    async def update_group(self, seller_id: uuid.UUID, group_id: uuid.UUID, *, title: str, kind: str) -> None:
        await self._enrolled(seller_id)
        title, kind = self._group_fields(title, kind)
        if not await self.stocks.update_group(seller_id, group_id, title=title, kind=kind):
            raise BoardConflictError("Группа не найдена")
        await self.session.commit()

    async def delete_group(self, seller_id: uuid.UUID, group_id: uuid.UUID) -> None:
        await self._enrolled(seller_id)
        if not await self.stocks.delete_group(seller_id, group_id):
            raise BoardConflictError("Группа не найдена")
        await self.session.commit()

    async def reorder_groups(self, seller_id: uuid.UUID, group_ids: Sequence[uuid.UUID]) -> None:
        await self._enrolled(seller_id)
        known = {group.id for group in await self.stocks.groups(seller_id)}
        if set(group_ids) != known or len(set(group_ids)) != len(group_ids):
            raise BoardConflictError("Порядок должен перечислить каждую группу кабинета по одному разу")
        await self.stocks.set_group_order(seller_id, group_ids)
        await self.session.commit()

    async def set_group_columns(self, seller_id: uuid.UUID, group_id: uuid.UUID, warehouse_ids: Sequence[int]) -> None:
        """Какие склады стоят в группе и в каком порядке."""
        await self._enrolled(seller_id)
        if await self.stocks.group(seller_id, group_id) is None:
            raise BoardConflictError("Группа не найдена")
        known = {warehouse.warehouse_id: warehouse for warehouse in await self.stocks.warehouses(seller_id)}
        for warehouse_id in warehouse_ids:
            warehouse = known.get(warehouse_id)
            if warehouse is None:
                raise BoardConflictError(f"Склада {warehouse_id} нет в кабинете")
            if warehouse.is_deleting:
                # Остаток на нём не спрашивается, и столбец показал бы ноль, которого не мерили.
                raise BoardConflictError(f"Склад «{warehouse.name}» удаляется из кабинета, в таблицу его не поставить")
        await self.stocks.set_group_columns(seller_id, group_id, warehouse_ids)
        await self.session.commit()

    # --- barcodes ----------------------------------------------------------------

    async def add_barcodes(self, seller_id: uuid.UUID, raw: Sequence[str], added_by: uuid.UUID | None) -> int:
        """Вписать баркоды. Остаток по ним подтянет ближайший сбор или кнопка «Обновить»."""
        await self._enrolled(seller_id)
        barcodes = []
        for value in raw:
            barcode = value.strip()
            if not barcode:
                continue
            if not BARCODE.match(barcode):
                raise BoardConflictError(f"«{barcode[:40]}» не похоже на баркод")
            barcodes.append(barcode)
        added = await self.stocks.add_barcodes(seller_id, barcodes, added_by)
        await self.session.commit()
        return added

    async def remove_barcode(self, seller_id: uuid.UUID, barcode: str) -> None:
        await self._enrolled(seller_id)
        if not await self.stocks.remove_barcode(seller_id, barcode):
            raise BoardConflictError("Такого баркода в таблице нет")
        await self.session.commit()

    async def set_note(self, seller_id: uuid.UUID, barcode: str, note: str, updated_by: uuid.UUID | None) -> None:
        await self._enrolled(seller_id)
        if not await self.stocks.set_note(seller_id, barcode, note.strip(), updated_by):
            raise BoardConflictError("Такого баркода в таблице нет")
        await self.session.commit()

    # --- export and refresh -------------------------------------------------------

    async def export(self) -> BoardReportFile:
        views = await self.views()
        day = datetime.now(self.timezone).date()
        content = await asyncio.to_thread(render_workbook, views)
        return BoardReportFile(day, content)

    async def request_refresh(self, seller_id: uuid.UUID, requested_by: uuid.UUID | None = None) -> RefreshRequest:
        await self._enrolled(seller_id)
        request = await self.stocks.request_refresh(seller_id, requested_by)
        await self.session.commit()
        return self._refresh(request)

    async def request_refresh_all(self, requested_by: uuid.UUID | None = None) -> int:
        """Поставить в очередь все подключённые кабинеты — для листа сравнения."""
        enrolled = await self._enrolled_ids()
        for seller_id in enrolled:
            await self.stocks.request_refresh(seller_id, requested_by)
        await self.session.commit()
        return len(enrolled)

    async def refresh_state(self, seller_id: uuid.UUID) -> RefreshRequest | None:
        await self._enrolled(seller_id)
        request = await self.stocks.latest_refresh(seller_id)
        return self._refresh(request) if request else None

    # --- helpers ---------------------------------------------------------------------

    async def _groups(self, seller_id: uuid.UUID) -> tuple[GroupView, ...]:
        names = {warehouse.warehouse_id: warehouse.name for warehouse in await self.stocks.warehouses(seller_id)}
        columns = await self.stocks.columns(seller_id)
        return tuple(
            GroupView(
                id=group.id,
                title=group.title,
                kind=group.kind,
                columns=tuple(
                    ColumnView(warehouse_id=column.warehouse_id, name=names[column.warehouse_id])
                    for column in columns
                    if column.group_id == group.id and column.warehouse_id in names
                ),
            )
            for group in await self.stocks.groups(seller_id)
        )

    async def _enrolled_ids(self) -> set[uuid.UUID]:
        tracked = await self.stocks.tracked_seller_ids()
        active = {seller.id for seller in await self.sellers.list_sellers()}
        return tracked & active

    async def _enrolled(self, seller_id: uuid.UUID) -> str:
        seller = await self.sellers.get(seller_id)
        if seller is None or seller.archived_at is not None:
            raise SellerNotFoundError
        if await self.stocks.tracked(seller_id) is None:
            raise SellerNotFoundError
        return seller.name

    @staticmethod
    def _group_fields(title: str, kind: str) -> tuple[str, str]:
        title = title.strip()
        if not title:
            raise BoardConflictError("У группы должно быть название")
        if kind not in GROUP_KINDS:
            raise BoardConflictError("Неизвестный вид группы")
        return title[:255], kind

    @staticmethod
    def _refresh(request: RefreshRequestModel) -> RefreshRequest:
        return RefreshRequest(
            status=request.status,
            requested_at=request.requested_at or datetime.now(UTC),
            finished_at=request.finished_at,
            error=request.error,
        )
