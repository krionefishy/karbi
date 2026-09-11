import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_fbs_stocks.domain import BoardBarcode, Column, ColumnGroup, SellerWarehouse, StockFact
from backend.modules.wb_fbs_stocks.infrastructure.postgres.models import (
    BarcodeModel,
    ColumnModel,
    GroupModel,
    RefreshRequestModel,
    StockFactModel,
    TrackedSellerModel,
    WarehouseModel,
)

_CHUNK = 500


class FbsStocksRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- membership -------------------------------------------------------

    async def tracked_seller_ids(self) -> set[uuid.UUID]:
        return set(await self.session.scalars(select(TrackedSellerModel.seller_id)))

    async def tracked(self, seller_id: uuid.UUID) -> TrackedSellerModel | None:
        return await self.session.get(TrackedSellerModel, seller_id)

    async def track(self, seller_id: uuid.UUID) -> None:
        statement = insert(TrackedSellerModel).values(seller_id=seller_id)
        await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id"]))

    async def untrack(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(delete(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id))

    async def purge_seller(self, seller_id: uuid.UUID) -> None:
        """Всё, что модуль знает о кабинете, включая группы и заметки селлера."""
        for model in (StockFactModel, ColumnModel, GroupModel, BarcodeModel, WarehouseModel, RefreshRequestModel):
            await self.session.execute(delete(model).where(model.seller_id == seller_id))
        await self.untrack(seller_id)

    async def still_tracked(self, seller_id: uuid.UUID) -> bool:
        """Подключён ли кабинет в момент записи, с блокировкой строки.

        Отключение может прийти, пока читался WB; FOR SHARE заставляет purge
        дождаться этой транзакции или эту — увидеть строку исчезнувшей, и запись
        не воскресит то, что отключение только что стёрло.
        """
        found = await self.session.scalar(
            select(TrackedSellerModel.seller_id)
            .where(TrackedSellerModel.seller_id == seller_id)
            .with_for_update(read=True)
        )
        return found is not None

    # --- collection schedule ---------------------------------------------

    async def sellers_due(self, since: datetime, *, retry_after: datetime) -> list[uuid.UUID]:
        """Кабинеты, не собранные с `since` и не пробованные с `retry_after`."""
        rows = await self.session.scalars(
            select(TrackedSellerModel.seller_id)
            .where(
                (TrackedSellerModel.collected_at.is_(None)) | (TrackedSellerModel.collected_at < since),
                (TrackedSellerModel.attempted_at.is_(None)) | (TrackedSellerModel.attempted_at < retry_after),
            )
            .order_by(TrackedSellerModel.enrolled_at)
        )
        return list(rows)

    async def record_attempt(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(attempted_at=datetime.now(UTC))
        )

    async def finish_collection(self, seller_id: uuid.UUID, warning: str | None, *, now: datetime) -> None:
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(collected_at=now, collection_error=warning[:1000] if warning else None)
        )

    async def fail_collection(self, seller_id: uuid.UUID, error: str) -> None:
        """Прежние остатки и их дата остаются; меняется только текст ошибки."""
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(collection_error=error[:1000])
        )

    async def collection_summary(self, seller_ids: set[uuid.UUID]) -> tuple[datetime | None, int, datetime | None, int]:
        """Последний успех, число кабинетов с ошибкой, самый старый сбор и сколько не собрано ни разу."""
        if not seller_ids:
            return None, 0, None, 0
        row = (
            await self.session.execute(
                select(
                    func.max(TrackedSellerModel.collected_at),
                    func.count().filter(TrackedSellerModel.collection_error.is_not(None)),
                    func.min(TrackedSellerModel.collected_at),
                    func.count().filter(TrackedSellerModel.collected_at.is_(None)),
                ).where(TrackedSellerModel.seller_id.in_(seller_ids))
            )
        ).one()
        return row[0], int(row[1] or 0), row[2], int(row[3] or 0)

    # --- warehouses --------------------------------------------------------

    async def replace_warehouses(self, seller_id: uuid.UUID, warehouses: Sequence[SellerWarehouse]) -> None:
        """Зеркало складов целиком. Столбец склада, пропавшего из кабинета или удаляемого, снимается.

        Удаляемый склад остаётся в зеркале (настройка покажет его с пометкой), но
        в таблице ему делать нечего: остаток на нём не спрашивается, и столбец с
        нулём выглядел бы как измеренный ноль.
        """
        if not warehouses:
            # Пустой список — не «складов нет», а неответ: снять по нему все столбцы
            # значило бы стереть настройку оператора из-за одного сбоя шлюза.
            raise ValueError("Пустой список складов не принимается")
        await self.session.execute(delete(WarehouseModel).where(WarehouseModel.seller_id == seller_id))
        now = datetime.now(UTC)
        unique = {warehouse.warehouse_id: warehouse for warehouse in warehouses}
        rows = [
            {
                "seller_id": seller_id,
                "warehouse_id": warehouse.warehouse_id,
                "office_id": warehouse.office_id,
                "name": warehouse.name[:255],
                "delivery_type": warehouse.delivery_type,
                "is_deleting": warehouse.is_deleting,
                "seen_at": now,
            }
            for warehouse in unique.values()
        ]
        for offset in range(0, len(rows), _CHUNK):
            await self.session.execute(insert(WarehouseModel).values(rows[offset : offset + _CHUNK]))
        live = [warehouse.warehouse_id for warehouse in unique.values() if not warehouse.is_deleting]
        gone = delete(ColumnModel).where(ColumnModel.seller_id == seller_id)
        if live:
            gone = gone.where(ColumnModel.warehouse_id.not_in(live))
        await self.session.execute(gone)

    async def warehouses(self, seller_id: uuid.UUID) -> list[SellerWarehouse]:
        rows = await self.session.scalars(
            select(WarehouseModel).where(WarehouseModel.seller_id == seller_id).order_by(WarehouseModel.name)
        )
        return [
            SellerWarehouse(
                warehouse_id=row.warehouse_id,
                office_id=row.office_id,
                name=row.name,
                delivery_type=row.delivery_type,
                is_deleting=row.is_deleting,
            )
            for row in rows
        ]

    # --- groups and columns --------------------------------------------------

    async def groups(self, seller_id: uuid.UUID) -> list[ColumnGroup]:
        rows = await self.session.scalars(
            select(GroupModel).where(GroupModel.seller_id == seller_id).order_by(GroupModel.position, GroupModel.title)
        )
        return [ColumnGroup(id=row.id, title=row.title, kind=row.kind, position=row.position) for row in rows]

    async def group(self, seller_id: uuid.UUID, group_id: uuid.UUID) -> GroupModel | None:
        row = await self.session.get(GroupModel, group_id)
        return row if row is not None and row.seller_id == seller_id else None

    async def add_group(self, seller_id: uuid.UUID, title: str, kind: str) -> ColumnGroup:
        # coalesce отдаёт ровно одно число: -1 на пустой таблице, иначе последнюю позицию.
        last = (
            await self.session.execute(
                select(func.coalesce(func.max(GroupModel.position), -1)).where(GroupModel.seller_id == seller_id)
            )
        ).scalar_one()
        row = GroupModel(seller_id=seller_id, title=title, kind=kind, position=int(last) + 1)
        self.session.add(row)
        await self.session.flush()
        return ColumnGroup(id=row.id, title=row.title, kind=row.kind, position=row.position)

    async def update_group(self, seller_id: uuid.UUID, group_id: uuid.UUID, *, title: str, kind: str) -> bool:
        result = await self.session.execute(
            update(GroupModel)
            .where(GroupModel.id == group_id, GroupModel.seller_id == seller_id)
            .values(title=title, kind=kind)
            .returning(GroupModel.id)
        )
        return bool(result.all())

    async def delete_group(self, seller_id: uuid.UUID, group_id: uuid.UUID) -> bool:
        """Группа уходит со своими столбцами: склады остаются в зеркале, но не показываются."""
        await self.session.execute(
            delete(ColumnModel).where(ColumnModel.seller_id == seller_id, ColumnModel.group_id == group_id)
        )
        result = await self.session.execute(
            delete(GroupModel)
            .where(GroupModel.id == group_id, GroupModel.seller_id == seller_id)
            .returning(GroupModel.id)
        )
        return bool(result.all())

    async def set_group_order(self, seller_id: uuid.UUID, group_ids: Sequence[uuid.UUID]) -> None:
        for position, group_id in enumerate(group_ids):
            await self.session.execute(
                update(GroupModel)
                .where(GroupModel.id == group_id, GroupModel.seller_id == seller_id)
                .values(position=position)
            )

    async def columns(self, seller_id: uuid.UUID) -> list[Column]:
        rows = await self.session.scalars(
            select(ColumnModel).where(ColumnModel.seller_id == seller_id).order_by(ColumnModel.position)
        )
        return [Column(warehouse_id=row.warehouse_id, group_id=row.group_id, position=row.position) for row in rows]

    async def set_group_columns(self, seller_id: uuid.UUID, group_id: uuid.UUID, warehouse_ids: Sequence[int]) -> None:
        """Состав группы целиком, в заданном порядке.

        Склад стоит в одной группе: попав сюда, он уходит из прежней. Целиком,
        а не по одному, потому что позиции — это порядок списка, а не числа,
        которые оператор придумывает сам.
        """
        await self.session.execute(
            delete(ColumnModel).where(
                ColumnModel.seller_id == seller_id,
                (ColumnModel.group_id == group_id) | (ColumnModel.warehouse_id.in_(list(warehouse_ids))),
            )
        )
        rows = [
            {"seller_id": seller_id, "warehouse_id": warehouse_id, "group_id": group_id, "position": position}
            for position, warehouse_id in enumerate(dict.fromkeys(warehouse_ids))
        ]
        if rows:
            await self.session.execute(insert(ColumnModel).values(rows))

    # --- barcodes ------------------------------------------------------------

    async def barcodes(self, seller_id: uuid.UUID) -> list[BoardBarcode]:
        rows = await self.session.scalars(
            select(BarcodeModel)
            .where(BarcodeModel.seller_id == seller_id)
            .order_by(BarcodeModel.position, BarcodeModel.added_at, BarcodeModel.barcode)
        )
        return [
            BoardBarcode(barcode=row.barcode, note=row.note, position=row.position, added_at=row.added_at)
            for row in rows
        ]

    async def add_barcodes(self, seller_id: uuid.UUID, barcodes: Sequence[str], added_by: uuid.UUID | None) -> int:
        """Дописать баркоды в конец списка. Уже вписанные пропускаются, не задваиваются."""
        position = int(
            (
                await self.session.execute(
                    select(func.coalesce(func.max(BarcodeModel.position), -1)).where(
                        BarcodeModel.seller_id == seller_id
                    )
                )
            ).scalar_one()
        )
        added = 0
        now = datetime.now(UTC)
        for barcode in dict.fromkeys(barcodes):
            position += 1
            statement = insert(BarcodeModel).values(
                seller_id=seller_id, barcode=barcode, position=position, added_by=added_by, added_at=now
            )
            result = await self.session.execute(
                statement.on_conflict_do_nothing(index_elements=["seller_id", "barcode"]).returning(
                    BarcodeModel.barcode
                )
            )
            added += len(result.all())
        return added

    async def remove_barcode(self, seller_id: uuid.UUID, barcode: str) -> bool:
        await self.session.execute(
            delete(StockFactModel).where(StockFactModel.seller_id == seller_id, StockFactModel.barcode == barcode)
        )
        result = await self.session.execute(
            delete(BarcodeModel)
            .where(BarcodeModel.seller_id == seller_id, BarcodeModel.barcode == barcode)
            .returning(BarcodeModel.barcode)
        )
        return bool(result.all())

    async def set_note(self, seller_id: uuid.UUID, barcode: str, note: str, updated_by: uuid.UUID | None) -> bool:
        result = await self.session.execute(
            update(BarcodeModel)
            .where(BarcodeModel.seller_id == seller_id, BarcodeModel.barcode == barcode)
            .values(note=note, note_updated_by=updated_by, note_updated_at=datetime.now(UTC))
            .returning(BarcodeModel.barcode)
        )
        return bool(result.all())

    # --- stock facts ---------------------------------------------------------

    async def replace_facts(self, seller_id: uuid.UUID, facts: Sequence[StockFact], *, now: datetime) -> None:
        await self.session.execute(delete(StockFactModel).where(StockFactModel.seller_id == seller_id))
        unique = {(fact.warehouse_id, fact.barcode): fact for fact in facts}
        rows = [
            {
                "seller_id": seller_id,
                "warehouse_id": fact.warehouse_id,
                "barcode": fact.barcode,
                "amount": fact.amount,
                "collected_at": now,
            }
            for fact in unique.values()
        ]
        for offset in range(0, len(rows), _CHUNK):
            await self.session.execute(insert(StockFactModel).values(rows[offset : offset + _CHUNK]))

    async def facts(self, seller_id: uuid.UUID) -> dict[tuple[int, str], int]:
        rows = await self.session.execute(
            select(StockFactModel.warehouse_id, StockFactModel.barcode, StockFactModel.amount).where(
                StockFactModel.seller_id == seller_id
            )
        )
        return {(int(warehouse_id), str(barcode)): int(amount) for warehouse_id, barcode, amount in rows.all()}

    # --- manual refresh --------------------------------------------------

    async def request_refresh(self, seller_id: uuid.UUID, requested_by: uuid.UUID | None) -> RefreshRequestModel:
        """Поставить запрос или вернуть тот, что уже ждёт.

        Два одновременных нажатия оба не найдут активного запроса; уникальный
        индекс остановит второй, и вместо ошибки он получает первый.
        """
        pending = await self.active_refresh(seller_id)
        if pending is not None:
            return pending
        request = RefreshRequestModel(seller_id=seller_id, requested_by=requested_by)
        try:
            async with self.session.begin_nested():
                self.session.add(request)
                await self.session.flush()
        except IntegrityError:
            raced = await self.active_refresh(seller_id)
            if raced is None:
                raise
            return raced
        return request

    async def active_refresh(self, seller_id: uuid.UUID) -> RefreshRequestModel | None:
        return await self.session.scalar(
            select(RefreshRequestModel)
            .where(
                RefreshRequestModel.seller_id == seller_id,
                RefreshRequestModel.status.in_(("queued", "running")),
            )
            .order_by(RefreshRequestModel.requested_at)
            .limit(1)
        )

    async def latest_refresh(self, seller_id: uuid.UUID) -> RefreshRequestModel | None:
        return await self.session.scalar(
            select(RefreshRequestModel)
            .where(RefreshRequestModel.seller_id == seller_id)
            .order_by(RefreshRequestModel.requested_at.desc())
            .limit(1)
        )

    async def claim_refreshes(self, limit: int = 5) -> list[RefreshRequestModel]:
        requests = list(
            await self.session.scalars(
                select(RefreshRequestModel)
                .where(RefreshRequestModel.status == "queued")
                .order_by(RefreshRequestModel.requested_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        )
        for request in requests:
            request.status = "running"
            request.started_at = datetime.now(UTC)
        return requests

    async def abandon_stale_refreshes(self, started_before: datetime) -> int:
        result = await self.session.execute(
            update(RefreshRequestModel)
            .where(RefreshRequestModel.status == "running", RefreshRequestModel.started_at < started_before)
            .values(status="error", error="Сбор прервался: воркер перезапустился", finished_at=datetime.now(UTC))
            .returning(RefreshRequestModel.id)
        )
        return len(result.all())

    async def finish_refresh(self, request_id: uuid.UUID, error: str | None = None) -> None:
        await self.session.execute(
            update(RefreshRequestModel)
            .where(RefreshRequestModel.id == request_id)
            .values(
                status="error" if error else "success",
                error=error[:1000] if error else None,
                finished_at=datetime.now(UTC),
            )
        )
