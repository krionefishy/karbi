import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.domain import ReviewFact, StockFact
from backend.modules.wb_core.infrastructure.postgres.models import (
    FbsWarehouseStockModel,
    MirrorStateModel,
    ReviewFactModel,
    StockFactModel,
)

_INSERT_CHUNK = 1000


class MirrorRepository:
    """Зеркало WB по селлеру: отметки сбора и сами факты."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- schedule -------------------------------------------------------------

    async def states(self, kind: str) -> dict[uuid.UUID, MirrorStateModel]:
        rows = await self.session.scalars(select(MirrorStateModel).where(MirrorStateModel.kind == kind))
        return {row.seller_id: row for row in rows}

    async def state(self, seller_id: uuid.UUID, kind: str) -> MirrorStateModel | None:
        return await self.session.get(MirrorStateModel, (seller_id, kind))

    async def sellers_due(
        self,
        kind: str,
        candidates: Iterable[uuid.UUID],
        *,
        since: datetime,
        retry_after: datetime,
    ) -> list[uuid.UUID]:
        """Кто из кандидатов не собран с `since` и не пробован с `retry_after`.

        Селлер без строки состояния — новый, и он в очереди сразу: после
        деплоя зеркало наполняется за один оборот, а не к следующему слоту.
        """
        states = await self.states(kind)
        due: list[uuid.UUID] = []
        for seller_id in candidates:
            row = states.get(seller_id)
            if row is None:
                due.append(seller_id)
                continue
            collected = row.collected_at is not None and row.collected_at >= since
            attempted = row.attempted_at is not None and row.attempted_at >= retry_after
            if not collected and not attempted:
                due.append(seller_id)
        return due

    async def record_attempt(self, seller_id: uuid.UUID, kind: str, *, now: datetime) -> None:
        statement = insert(MirrorStateModel).values(seller_id=seller_id, kind=kind, attempted_at=now)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["seller_id", "kind"], set_={"attempted_at": statement.excluded.attempted_at}
            )
        )

    async def mark_collected(self, seller_id: uuid.UUID, kind: str, *, now: datetime) -> None:
        await self.session.execute(
            update(MirrorStateModel)
            .where(MirrorStateModel.seller_id == seller_id, MirrorStateModel.kind == kind)
            .values(collected_at=now, error=None)
        )

    async def mark_failed(self, seller_id: uuid.UUID, kind: str, error: str) -> None:
        """Прежние факты и их дата остаются; меняется только текст ошибки."""
        await self.session.execute(
            update(MirrorStateModel)
            .where(MirrorStateModel.seller_id == seller_id, MirrorStateModel.kind == kind)
            .values(error=error[:1000])
        )

    # --- stocks -----------------------------------------------------------------

    async def replace_stocks(
        self,
        seller_id: uuid.UUID,
        facts: Iterable[StockFact],
        per_warehouse: Mapping[tuple[str, int], int],
        *,
        now: datetime,
    ) -> None:
        """Остатки целиком: карточка, пропавшая из ответа, пропадает и отсюда."""
        await self.session.execute(delete(StockFactModel).where(StockFactModel.seller_id == seller_id))
        await self.session.execute(delete(FbsWarehouseStockModel).where(FbsWarehouseStockModel.seller_id == seller_id))
        await self._insert(
            StockFactModel,
            [
                {
                    "seller_id": seller_id,
                    "article": fact.article,
                    "fbo_quantity": fact.fbo_quantity,
                    "fbo_quantity_full": fact.fbo_quantity_full,
                    "fbs_quantity": fact.fbs_quantity,
                    "collected_at": now,
                }
                for fact in facts
            ],
        )
        await self._insert(
            FbsWarehouseStockModel,
            [
                {
                    "seller_id": seller_id,
                    "article": article,
                    "warehouse_id": warehouse_id,
                    "quantity": quantity,
                    "collected_at": now,
                }
                for (article, warehouse_id), quantity in per_warehouse.items()
            ],
        )

    async def stocks(self, seller_id: uuid.UUID) -> dict[str, StockFact]:
        rows = await self.session.scalars(select(StockFactModel).where(StockFactModel.seller_id == seller_id))
        return {
            row.article: StockFact(
                article=row.article,
                fbo_quantity=row.fbo_quantity,
                fbo_quantity_full=row.fbo_quantity_full,
                fbs_quantity=row.fbs_quantity,
            )
            for row in rows
        }

    async def fbs_warehouse_stocks(self, seller_id: uuid.UUID) -> dict[tuple[str, int], int]:
        rows = await self.session.scalars(
            select(FbsWarehouseStockModel).where(FbsWarehouseStockModel.seller_id == seller_id)
        )
        return {(row.article, row.warehouse_id): row.quantity for row in rows}

    # --- reviews ----------------------------------------------------------------

    async def replace_reviews(self, seller_id: uuid.UUID, facts: Iterable[ReviewFact], *, now: datetime) -> None:
        await self.session.execute(delete(ReviewFactModel).where(ReviewFactModel.seller_id == seller_id))
        await self._insert(
            ReviewFactModel,
            [
                {
                    "seller_id": seller_id,
                    "article": fact.article,
                    "count_rating_1": fact.ratings[0],
                    "count_rating_2": fact.ratings[1],
                    "count_rating_3": fact.ratings[2],
                    "count_rating_4": fact.ratings[3],
                    "count_rating_5": fact.ratings[4],
                    "count_with_photo": fact.with_photo,
                    "count_with_video": fact.with_video,
                    "collected_at": now,
                }
                for fact in facts
            ],
        )

    async def reviews(self, seller_id: uuid.UUID) -> dict[str, ReviewFact]:
        rows = await self.session.scalars(select(ReviewFactModel).where(ReviewFactModel.seller_id == seller_id))
        return {
            row.article: ReviewFact(
                article=row.article,
                ratings=(
                    row.count_rating_1,
                    row.count_rating_2,
                    row.count_rating_3,
                    row.count_rating_4,
                    row.count_rating_5,
                ),
                with_photo=row.count_with_photo,
                with_video=row.count_with_video,
            )
            for row in rows
        }

    # --- catalog ----------------------------------------------------------------

    async def lock_catalog(self, seller_id: uuid.UUID) -> None:
        """Транзакционная блокировка на селлера: вторая запись каталога ждёт первую."""
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:seller_id))"), {"seller_id": str(seller_id)}
        )

    async def _insert(self, model: Any, rows: list[dict[str, Any]]) -> None:
        for offset in range(0, len(rows), _INSERT_CHUNK):
            await self.session.execute(insert(model).values(rows[offset : offset + _INSERT_CHUNK]))
