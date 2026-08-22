import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

from backend.modules.notifications.application import BotRegistry
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.modules.wb_turnover.application import CalculationService, CollectionService, DigestService
from backend.modules.wb_turnover.infrastructure.postgres import TurnoverRepository
from backend.modules.wb_turnover.infrastructure.wb import WBMarketplaceClient, WBStatisticsClient
from backend.shared.heartbeat import touch_heartbeat
from backend.shared.security import CredentialCipher
from backend.shared.settings import Settings
from backend.storage.pg import Database

# A step that never finished cannot be claimed again, so anything still marked
# running after this long is closed on the next sweep.
STALE_RUN = timedelta(hours=6)
# The metric and the digest log are kept far longer than their raw inputs: they
# are what an alert is explained with months later, but not forever.
TURNOVER_RETENTION_DAYS = 180
NOTIFICATION_RETENTION_DAYS = 90


class TurnoverWorker:
    """The whole schedule of the automation: snapshots, orders, maths, digest.

    Slots are claimed through a unique key on (kind, date, slot), so a restart
    in the middle of the day repeats nothing and needs no leases: every step is
    a handful of requests per seller, not a full scan.
    """

    def __init__(
        self,
        database: Database,
        cipher: CredentialCipher,
        statistics: WBStatisticsClient,
        marketplace: WBMarketplaceClient,
        settings: Settings,
        now: Callable[[ZoneInfo], datetime] | None = None,
    ) -> None:
        self.database = database
        self.cipher = cipher
        self.statistics = statistics
        self.marketplace = marketplace
        self.settings = settings
        self.turnover = settings.turnover
        self.timezone = ZoneInfo(settings.turnover.timezone)
        self._now = now or (lambda timezone: datetime.now(timezone))
        self._stop = asyncio.Event()
        self.logger = structlog.get_logger("wb_turnover_worker")

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self.logger.info("worker_started")
        while not self._stop.is_set():
            touch_heartbeat()
            try:
                # Every cycle, not only at start: the worker that crashed and
                # the worker that sweeps up after it are rarely the same process.
                await self._close_stale_runs()
                await self.tick()
            except Exception:
                self.logger.exception("turnover_tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.worker.poll_interval_seconds)
            except TimeoutError:
                continue
        self.logger.info("worker_stopped")

    async def tick(self) -> None:
        now = self._now(self.timezone)
        due = [slot for slot, hour in enumerate(self.turnover.stock_slot_hours) if self.is_due(now, hour, 0)]
        if due:
            # Only the freshest due slot. After a long pause the older ones
            # would be four identical snapshots taken minutes apart — four
            # times the API budget for no extra information — so they are
            # simply left unclaimed.
            await self.collect_stocks(now.date(), due[-1])
        if self.is_due(now, self.turnover.orders_hour, self.turnover.orders_minute):
            await self.collect_orders(now)
        if self.is_due(now, self.turnover.calculation_hour, self.turnover.calculation_minute):
            await self.calculate(now.date())
        if self.is_due(now, self.turnover.digest_hour, self.turnover.digest_minute):
            await self.send_digests(now.date())

    @staticmethod
    def is_due(now: datetime, hour: int, minute: int) -> bool:
        """Whether that moment of the day has passed. Claiming the slot decides the rest."""
        return (now.hour, now.minute) >= (hour, minute)

    async def collect_stocks(self, day: date, slot: int) -> None:
        async def collect(seller_id: uuid.UUID, service: CollectionService) -> None:
            await service.collect_stocks(seller_id, day, slot)

        await self._for_each_seller("stocks", day, slot, collect)

    async def collect_orders(self, now: datetime) -> None:
        async def collect(seller_id: uuid.UUID, service: CollectionService) -> None:
            await service.collect_orders(
                seller_id,
                now.astimezone(UTC),
                backfill_days=self.turnover.orders_backfill_days,
                overlap_hours=self.turnover.orders_overlap_hours,
            )

        await self._for_each_seller("orders", now.date(), 0, collect)

    async def calculate(self, day: date) -> None:
        run_id, seller_ids = await self._claim("calculation", day, 0)
        if run_id is None:
            return
        failed, error = 0, None
        for seller_id in seller_ids:
            try:
                async with self.database.session() as session:
                    turnover = TurnoverRepository(session)
                    service = CalculationService(session, turnover, window_days=self.turnover.orders_window_days)
                    rows = await service.calculate(seller_id, day)
                self.logger.info("turnover_calculated", seller_id=str(seller_id), articles=len(rows))
            except Exception as failure:
                failed += 1
                error = str(failure)
                self.logger.exception("turnover_calculation_failed", seller_id=str(seller_id))
        await self._prune(day)
        await self._finish(run_id, len(seller_ids), failed, error)

    async def send_digests(self, day: date) -> None:
        run_id, seller_ids = await self._claim("digest", day, 0)
        if run_id is None:
            return
        failed, error = 0, None
        for seller_id in seller_ids:
            try:
                async with self.database.session() as session:
                    service = DigestService(
                        session,
                        SellerRepository(session),
                        TurnoverRepository(session),
                        BotRegistry(session, NotificationRepository(session)),
                        threshold_days=self.turnover.threshold_days,
                        bot_code=self.turnover.notification_bot,
                    )
                    result = await service.send(seller_id, day)
                if result.sent:
                    self.logger.info("turnover_digest_sent", seller_id=str(seller_id), articles=result.articles)
            except Exception as failure:
                failed += 1
                error = str(failure)
                self.logger.exception("turnover_digest_failed", seller_id=str(seller_id))
        await self._finish(run_id, len(seller_ids), failed, error)

    async def _for_each_seller(self, kind: str, day: date, slot: int, action) -> None:
        run_id, seller_ids = await self._claim(kind, day, slot)
        if run_id is None:
            return
        failed, error = 0, None
        for seller_id in seller_ids:
            try:
                async with self.database.session() as session:
                    service = CollectionService(
                        session,
                        SellerRepository(session),
                        TurnoverRepository(session),
                        self.cipher,
                        self.statistics,
                        self.marketplace,
                    )
                    await action(seller_id, service)
            except (WBPermanentError, WBTemporaryError) as failure:
                # One seller's key or one rate limit must not cost the others
                # their slot; the next slot is a few hours away.
                failed += 1
                error = str(failure)
                self.logger.warning("turnover_collection_failed", seller_id=str(seller_id), error=str(failure))
            except Exception as failure:
                failed += 1
                error = str(failure)
                self.logger.exception("turnover_collection_crashed", seller_id=str(seller_id))
        self.logger.info("turnover_collected", kind=kind, sellers=len(seller_ids), failed=failed)
        await self._finish(run_id, len(seller_ids), failed, error)

    async def _claim(self, kind: str, day: date, slot: int) -> tuple[uuid.UUID | None, list[uuid.UUID]]:
        async with self.database.session() as session:
            turnover = TurnoverRepository(session)
            run = await turnover.start_run(kind, day, slot)
            if run is None:
                await session.rollback()
                return None, []
            run_id = run.id
            tracked = await turnover.tracked_seller_ids()
            active = {seller.id for seller in await SellerRepository(session).list_sellers()}
            await session.commit()
        return run_id, sorted(tracked & active)

    async def _finish(self, run_id: uuid.UUID, sellers: int, failed: int, error: str | None) -> None:
        async with self.database.session() as session:
            await TurnoverRepository(session).finish_run(run_id, sellers=sellers, failed=failed, error=error)
            await session.commit()

    async def _prune(self, day: date) -> None:
        async with self.database.session() as session:
            await TurnoverRepository(session).prune(
                snapshots_before=day - timedelta(days=self.turnover.snapshot_retention_days),
                orders_before=day - timedelta(days=self.turnover.order_retention_days),
                turnover_before=day - timedelta(days=TURNOVER_RETENTION_DAYS),
                notifications_before=day - timedelta(days=NOTIFICATION_RETENTION_DAYS),
            )
            await session.commit()

    async def _close_stale_runs(self) -> None:
        """A step interrupted by a restart would otherwise sit as running forever.

        All of them, not just the latest: a crash in the middle of a busy day
        can leave several kinds hanging at once.
        """
        async with self.database.session() as session:
            turnover = TurnoverRepository(session)
            stale = await turnover.running_runs_started_before(datetime.now(UTC) - STALE_RUN)
            for run in stale:
                await turnover.finish_run(run.id, sellers=run.sellers, failed=run.sellers, error="Прервано рестартом")
            if stale:
                await session.commit()
