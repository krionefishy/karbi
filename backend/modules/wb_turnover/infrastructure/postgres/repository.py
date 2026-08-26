import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_turnover.domain import ArticleOrders, ArticleStock, TurnoverRow
from backend.modules.wb_turnover.infrastructure.postgres.models import (
    CollectionRunModel,
    NotificationLogModel,
    OrderModel,
    RefreshRequestModel,
    SellerWarehouseModel,
    StockSnapshotModel,
    TrackedSellerModel,
    TurnoverDailyModel,
)

_CHUNK = 1000
# A run still marked running after this long has crashed mid-flight; its slot
# may be claimed again instead of staying burnt for the rest of the day.
RECLAIM_RUNNING_AFTER = timedelta(minutes=60)


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

    async def still_tracked(self, seller_id: uuid.UUID) -> bool:
        """Whether the seller survived until the write phase, locking the row.

        The seller list is snapshotted when a run claims its slot, so a purge
        can land between the snapshot and the write and the write would then
        resurrect the purged rows. FOR SHARE makes the two serialise: either
        the purge already deleted the row and we see it gone, or its DELETE
        waits until this transaction commits.
        """
        found = await self.session.scalar(
            select(TrackedSellerModel.seller_id)
            .where(TrackedSellerModel.seller_id == seller_id)
            .with_for_update(read=True)
        )
        return found is not None

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
        """The latest collected snapshot of each article, split by delivery model.

        Both models come from the same date and slot — the last one taken. Picking
        the freshest row per model independently would happily add yesterday's
        FBS figure to this morning's FBO and call the sum the current stock.
        """
        rows = await self.session.execute(
            text(
                """
                WITH latest AS (
                    SELECT snapshot_date, slot
                      FROM wb_turnover.stock_snapshots
                     WHERE seller_id = :seller_id AND snapshot_date >= :since
                     ORDER BY snapshot_date DESC, slot DESC
                     LIMIT 1
                )
                SELECT article, delivery_model, quantity
                  FROM wb_turnover.stock_snapshots
                  JOIN latest USING (snapshot_date, slot)
                 WHERE seller_id = :seller_id
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

    async def average_stock(self, seller_id: uuid.UUID, since: date, until: date) -> dict[str, tuple[float, int]]:
        """Average total stock per article over the window, and how many days it covers.

        Averaged over collection points, not days: that is the whole reason for
        taking several snapshots a day. Both bounds are inclusive — the orders
        window ends yesterday, so today's snapshots stay out of the average too.
        """
        rows = await self.session.execute(
            text(
                """
                WITH points AS (
                    SELECT article, snapshot_date, slot, SUM(quantity) AS total
                      FROM wb_turnover.stock_snapshots
                     WHERE seller_id = :seller_id
                       AND snapshot_date >= :since AND snapshot_date <= :until
                     GROUP BY article, snapshot_date, slot
                )
                SELECT article, AVG(total), COUNT(DISTINCT snapshot_date)
                  FROM points
                 GROUP BY article
                """
            ),
            {"seller_id": seller_id, "since": since, "until": until},
        )
        return {row[0]: (float(row[1] or 0), int(row[2] or 0)) for row in rows.all()}

    # --- orders -----------------------------------------------------------

    async def upsert_orders(self, seller_id: uuid.UUID, orders: Sequence[dict]) -> None:
        # One srid may arrive several times in a pull — pages overlap on their
        # boundary row, and an order can change twice inside the window. The last
        # row wins here, because "ON CONFLICT DO UPDATE cannot affect row a
        # second time" would otherwise fail the whole batch.
        deduplicated = {order["srid"]: order for order in orders}
        rows = [{**order, "seller_id": seller_id, "collected_at": datetime.now(UTC)} for order in deduplicated.values()]
        for offset in range(0, len(rows), _CHUNK):
            statement = insert(OrderModel).values(rows[offset : offset + _CHUNK])
            await self.session.execute(
                statement.on_conflict_do_update(
                    index_elements=["srid"],
                    set_={
                        # seller_id too: the same cabinet reconnected under a new
                        # seller gets its orders moved over instead of split.
                        "seller_id": statement.excluded.seller_id,
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

    async def prune(
        self, *, snapshots_before: date, orders_before: date, turnover_before: date, notifications_before: date
    ) -> None:
        await self.session.execute(
            delete(StockSnapshotModel).where(StockSnapshotModel.snapshot_date < snapshots_before)
        )
        await self.session.execute(delete(OrderModel).where(OrderModel.order_date < orders_before))
        await self.session.execute(delete(TurnoverDailyModel).where(TurnoverDailyModel.date < turnover_before))
        await self.session.execute(delete(NotificationLogModel).where(NotificationLogModel.date < notifications_before))

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
        """Claim a schedule slot. Returns None when this slot already ran.

        A slot whose run is still marked running after RECLAIM_RUNNING_AFTER is
        claimed again: that run crashed before finishing, and the slot must not
        stay burnt until the stale-run sweep. A finished run keeps its slot.
        """
        now = datetime.now(UTC)
        statement = (
            insert(CollectionRunModel)
            .values(id=uuid.uuid4(), kind=kind, run_date=run_date, slot=slot, trigger=trigger)
            .on_conflict_do_update(
                constraint="uq_wb_turnover_run_slot",
                set_={
                    "status": "running",
                    "sellers": 0,
                    "failed_sellers": 0,
                    "error": None,
                    "started_at": now,
                    "finished_at": None,
                },
                where=(CollectionRunModel.status == "running")
                & (CollectionRunModel.started_at < now - RECLAIM_RUNNING_AFTER),
            )
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

    async def running_runs_started_before(self, moment: datetime) -> list[CollectionRunModel]:
        return list(
            await self.session.scalars(
                select(CollectionRunModel).where(
                    CollectionRunModel.status == "running", CollectionRunModel.started_at < moment
                )
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

    # --- manual refresh --------------------------------------------------

    async def request_refresh(self, seller_id: uuid.UUID, requested_by: uuid.UUID | None) -> RefreshRequestModel:
        """Ask for an out-of-schedule collection, or hand back the one already waiting."""
        pending = await self.active_refresh(seller_id)
        if pending is not None:
            return pending
        request = RefreshRequestModel(seller_id=seller_id, requested_by=requested_by)
        self.session.add(request)
        await self.session.flush()
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
        """Take waiting requests. Skips locked rows so two ticks cannot collide."""
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
