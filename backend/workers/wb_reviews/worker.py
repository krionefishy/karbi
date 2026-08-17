import asyncio
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

import structlog

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_reviews.application import ReviewSyncService
from backend.modules.wb_reviews.infrastructure.postgres import ReviewSyncRepository
from backend.storage.pg import Database


class WBReviewsWorker:
    def __init__(
        self,
        database: Database,
        poll_interval_seconds: int,
        sync_hour: int,
        timezone: str,
        enabled: bool,
        now: Callable[[ZoneInfo], datetime] | None = None,
    ) -> None:
        self._database = database
        self._poll_interval_seconds = poll_interval_seconds
        self._sync_hour = sync_hour
        self._timezone = ZoneInfo(timezone)
        self._enabled = enabled
        self._now = now or (lambda timezone: datetime.now(timezone))
        self._stop = asyncio.Event()
        self._logger = structlog.get_logger("wb_reviews_worker")

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self._logger.info("worker_started")
        while not self._stop.is_set():
            if self._enabled:
                await self._schedule_if_due()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval_seconds)
            except TimeoutError:
                continue
        self._logger.info("worker_stopped")

    async def _schedule_if_due(self) -> None:
        now = self._now(self._timezone)
        if now.hour < self._sync_hour:
            return
        async with self._database.session() as session:
            reviews = ReviewSyncRepository(session)
            if await reviews.run_for_date(now.date()) is not None:
                return
            service = ReviewSyncService(session, SellerRepository(session), reviews)
            run = await service.request("scheduled", now.date())
            self._logger.info("review_sync_scheduled", run_id=str(run.id), status=run.status)
