import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_turnover.domain import ArticleOrders, ArticleStock, TurnoverRow
from backend.modules.wb_turnover.infrastructure.postgres.models import (
    CollectionRunModel,
    NotificationLogModel,
    OrderModel,
    SellerWarehouseModel,
    StockSnapshotModel,
    TrackedSellerModel,
    TurnoverDailyModel,
)

_CHUNK = 1000


class TurnoverRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- membership -------------------------------------------------------

    async def tracked_seller_ids(self) -> set[uuid.UUID]:
        return set(await self.session.scalars(select(TrackedSellerModel.seller_id)))

    async def track(self, seller_id: uuid.UUID) -> None:
        statement = insert(TrackedSellerModel).values(seller_id=seller_id)
        await self.session.execute(statement.on_conflict_do_nothing(index_elements=["seller_id"]))

    async def untrack(self, seller_id: uuid.UUID) -> None:
        await self.session.execute(delete(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id))

    async def purge_seller(self, seller_id: uuid.UUID) -> None:
        for model in (
            StockSnapshotModel,
            OrderModel,
            TurnoverDailyModel,
            SellerWarehouseModel,
            NotificationLogModel,
        ):
            await self.session.execute(delete(model).where(model.seller_id == seller_id))
        await self.untrack(seller_id)

    async def tracked(self, seller_id: uuid.UUID) -> TrackedSellerModel | None:
        return await self.session.get(TrackedSellerModel, seller_id)

    async def set_watermark(self, seller_id: uuid.UUID, moment: datetime) -> None:
        await self.session.execute(
            update(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id).values(orders_watermark=moment)
        )

    # --- warehouses -------------------------------------------------------

    async def replace_warehouses(self, seller_id: uuid.UUID, warehouses: Sequence[tuple[int, str]]) -> None:
        await self.session.execute(delete(SellerWarehouseModel).where(SellerWarehouseModel.seller_id == seller_id))
        for warehouse_id, name in warehouses:
            self.session.add(SellerWarehouseModel(seller_id=seller_id, warehouse_id=warehouse_id, name=name))
        await self.session.execute(
            update(TrackedSellerModel)
            .where(TrackedSellerModel.seller_id == seller_id)
            .values(warehouses_synced_at=datetime.now(UTC))
        )

    async def warehouses(self, seller_id: uuid.UUID) -> list[tuple[int, str]]:
        rows = await self.session.execute(
            select(SellerWarehouseModel.warehouse_id, SellerWarehouseModel.name).where(
                SellerWarehouseModel.seller_id == seller_id
            )
        )
        return [(row[0], row[1]) for row in rows.all()]

    # --- stock ------------------------------------------------------------

    async def upsert_snapshots(
        self,
        seller_id: uuid.UUID,
        snapshot_date: date,
        slot: int,
        delivery_model: str,
        quantities: dict[str, tuple[int, int, int, int]],
    ) -> None:
        """Write one collection slot. Re-running a slot overwrites it rather than
        adding a second point to the same moment of the day."""
        rows = [
            {
                "seller_id": seller_id,
                "article": article,
                "snapshot_date": snapshot_date,
                "slot": slot,
                "delivery_model": delivery_model,
                "quantity": values[0],
                "quantity_full": values[1],
                "in_way_to_client": values[2],
                "in_way_from_client": values[3],
                "collected_at": datetime.now(UTC),
            }
            for article, values in quantities.items()
        ]
        for offset in range(0, len(rows), _CHUNK):
            statement = insert(StockSnapshotModel).values(rows[offset : offset + _CHUNK])
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["seller_id", "article", "snapshot_date", "slot", "delivery_model"],
                    set_={
                        "quantity": statement.excluded.quantity,
                        "quantity_full": statement.excluded.quantity_full,
                        "in_way_to_client": statement.excluded.in_way_to_client,
                        "in_way_from_client": statement.excluded.in_way_from_client,
                        "collected_at": statement.excluded.collected_at,
                    },
                )
            )

    async def latest_stock(self, seller_id: uuid.UUID, since: date) -> dict[str, ArticleStock]:
        """The freshest snapshot of each article, split by delivery model."""
        rows = await self.session.execute(
            text(
                """
                SELECT DISTINCT ON (article, delivery_model)
                       article, delivery_model, quantity
                  FROM wb_turnover.stock_snapshots
                 WHERE seller_id = :seller_id AND snapshot_date >= :since
                 ORDER BY article, delivery_model, snapshot_date DESC, slot DESC
                """
            ),
            {"seller_id": seller_id, "since": since},
        )
        collected: dict[str, ArticleStock] = {}
        for article, delivery_model, quantity in rows.all():
            current = collected.get(article, ArticleStock(article, 0, 0))
            if delivery_model == "fbo":
                collected[article] = ArticleStock(article, int(quantity), current.fbs)
            else:
                collected[article] = ArticleStock(article, current.fbo, int(quantity))
        return collected

    async def average_stock(self, seller_id: uuid.UUID, since: date) -> dict[str, tuple[float, int]]:
        """Average total stock per article over the window, and how many days it covers.

        Averaged over collection points, not days: that is the whole reason for
        taking several snapshots a day.
        """
        rows = await self.session.execute(
            text(
                """
                WITH points AS (
                    SELECT article, snapshot_date, slot, SUM(quantity) AS total
                      FROM wb_turnover.stock_snapshots
                     WHERE seller_id = :seller_id AND snapshot_date >= :since
                     GROUP BY article, snapshot_date, slot
                )
                SELECT article, AVG(total), COUNT(DISTINCT snapshot_date)
                  FROM points
                 GROUP BY article
                """
            ),
            {"seller_id": seller_id, "since": since},
        )
        return {row[0]: (float(row[1] or 0), int(row[2] or 0)) for row in rows.all()}

    # --- orders -----------------------------------------------------------

    async def upsert_orders(self, seller_id: uuid.UUID, orders: Sequence[dict]) -> None:
        rows = [{**order, "seller_id": seller_id, "collected_at": datetime.now(UTC)} for order in orders]
        for offset in range(0, len(rows), _CHUNK):
            statement = insert(OrderModel).values(rows[offset : offset + _CHUNK])
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["srid"],
                    set_={
                        "article": statement.excluded.article,
                        "order_date": statement.excluded.order_date,
                        "last_change_date": statement.excluded.last_change_date,
                        "is_cancel": statement.excluded.is_cancel,
                        "price": statement.excluded.price,
                        "warehouse_type": statement.excluded.warehouse_type,
                        "collected_at": statement.excluded.collected_at,
                    },
                )
            )

    async def orders_in_window(self, seller_id: uuid.UUID, since: date, until: date) -> dict[str, ArticleOrders]:
        """Orders per article over whole days only — `until` is yesterday, because
        today is a few hours old and would drag every rate down."""
        rows = await self.session.execute(
            select(
                OrderModel.article,
                func.count().filter(~OrderModel.is_cancel),
                func.count().filter(OrderModel.is_cancel),
                func.min(OrderModel.order_date),
            )
            .where(
                OrderModel.seller_id == seller_id,
                OrderModel.order_date >= since,
                OrderModel.order_date <= until,
            )
            .group_by(OrderModel.article)
        )
        return {
            row[0]: ArticleOrders(article=row[0], orders=int(row[1]), cancelled=int(row[2]), first_order=row[3])
            for row in rows.all()
        }

    async def prune(self, *, snapshots_before: date, orders_before: date) -> None:
        await self.session.execute(
            delete(StockSnapshotModel).where(StockSnapshotModel.snapshot_date < snapshots_before)
        )
        await self.session.execute(delete(OrderModel).where(OrderModel.order_date < orders_before))

    # --- metric -----------------------------------------------------------

    async def upsert_turnover(self, rows: Sequence[TurnoverRow]) -> None:
        if not rows:
            return
        values = [
            {
                "seller_id": row.seller_id,
                "article": row.article,
                "date": row.date,
                "stock_fbo": row.stock_fbo,
                "stock_fbs": row.stock_fbs,
                "stock_total": row.stock_total,
                "avg_stock": row.avg_stock,
                "orders_count": row.orders_count,
                "cancelled_count": row.cancelled_count,
                "avg_daily_orders": row.avg_daily_orders,
                "days_of_cover": row.days_of_cover,
                "turnover_days": row.turnover_days,
                "stock_days": row.stock_days,
                "sales_days": row.sales_days,
                "status": row.status,
                "computed_at": datetime.now(UTC),
            }
            for row in rows
        ]
        for offset in range(0, len(values), _CHUNK):
            statement = insert(TurnoverDailyModel).values(values[offset : offset + _CHUNK])
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["seller_id", "article", "date"],
                    set_={
                        key: statement.excluded[key]
                        for key in (
                            "stock_fbo",
                            "stock_fbs",
                            "stock_total",
                            "avg_stock",
                            "orders_count",
                            "cancelled_count",
                            "avg_daily_orders",
                            "days_of_cover",
                            "turnover_days",
                            "stock_days",
                            "sales_days",
                            "status",
                            "computed_at",
                        )
                    },
                )
            )

    async def turnover_on(self, seller_id: uuid.UUID, day: date) -> list[TurnoverDailyModel]:
        return list(
            await self.session.scalars(
                select(TurnoverDailyModel)
                .where(TurnoverDailyModel.seller_id == seller_id, TurnoverDailyModel.date == day)
                .order_by(TurnoverDailyModel.days_of_cover.nulls_last(), TurnoverDailyModel.article)
            )
        )

    async def latest_turnover_date(self, seller_id: uuid.UUID) -> date | None:
        return await self.session.scalar(
            select(func.max(TurnoverDailyModel.date)).where(TurnoverDailyModel.seller_id == seller_id)
        )

    # --- runs and notifications ------------------------------------------

    async def start_run(
        self, kind: str, run_date: date, slot: int, trigger: str = "scheduled"
    ) -> CollectionRunModel | None:
        """Claim a schedule slot. Returns None when this slot already ran."""
        statement = (
            insert(CollectionRunModel)
            .values(id=uuid.uuid4(), kind=kind, run_date=run_date, slot=slot, trigger=trigger)
            .on_conflict_do_nothing(constraint="uq_wb_turnover_run_slot")
            .returning(CollectionRunModel.id)
        )
        run_id = (await self.session.execute(statement)).scalar_one_or_none()
        if run_id is None:
            return None
        return await self.session.get(CollectionRunModel, run_id)

    async def finish_run(self, run_id: uuid.UUID, *, sellers: int, failed: int, error: str | None = None) -> None:
        status = "error" if failed and not sellers - failed else "partial_success" if failed else "success"
        await self.session.execute(
            update(CollectionRunModel)
            .where(CollectionRunModel.id == run_id)
            .values(
                status=status,
                sellers=sellers,
                failed_sellers=failed,
                error=error[:1000] if error else None,
                finished_at=datetime.now(UTC),
            )
        )

    async def last_run(self, kind: str | None = None) -> CollectionRunModel | None:
        query = select(CollectionRunModel).order_by(CollectionRunModel.started_at.desc()).limit(1)
        if kind is not None:
            query = query.where(CollectionRunModel.kind == kind)
        return await self.session.scalar(query)

    async def last_success_at(self, kind: str) -> datetime | None:
        return await self.session.scalar(
            select(CollectionRunModel.finished_at)
            .where(
                CollectionRunModel.kind == kind,
                CollectionRunModel.status.in_(("success", "partial_success")),
                CollectionRunModel.finished_at.is_not(None),
            )
            .order_by(CollectionRunModel.finished_at.desc())
            .limit(1)
        )

    async def count_runs_since(self, moment: datetime) -> int:
        return int(
            await self.session.scalar(
                select(func.count()).select_from(CollectionRunModel).where(CollectionRunModel.started_at >= moment)
            )
            or 0
        )

    async def notification_logged(self, seller_id: uuid.UUID, day: date) -> bool:
        return await self.session.get(NotificationLogModel, (seller_id, day)) is not None

    async def log_notification(
        self, seller_id: uuid.UUID, day: date, articles_count: int, message_id: uuid.UUID
    ) -> bool:
        """Records that the digest went out. False means it already had."""
        statement = (
            insert(NotificationLogModel)
            .values(seller_id=seller_id, date=day, articles_count=articles_count, message_id=message_id)
            .on_conflict_do_nothing(index_elements=["seller_id", "date"])
            .returning(NotificationLogModel.seller_id)
        )
        return (await self.session.execute(statement)).scalar_one_or_none() is not None
