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
        sync_minute: int,
        timezone: str,
        enabled: bool,
        now: Callable[[ZoneInfo], datetime] | None = None,
        job_max_attempts: int = 3,
        run_max_age_seconds: int = 21_600,
    ) -> None:
        self._database = database
        self._poll_interval_seconds = poll_interval_seconds
        self._sync_hour = sync_hour
        self._sync_minute = sync_minute
        self._timezone = ZoneInfo(timezone)
        self._enabled = enabled
        self._now = now or (lambda timezone: datetime.now(timezone))
        self._job_max_attempts = job_max_attempts
        self._run_max_age_seconds = run_max_age_seconds
        self._stop = asyncio.Event()
        self._logger = structlog.get_logger("wb_reviews_worker")

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self._logger.info("worker_started")
        while not self._stop.is_set():
            if self._enabled:
                await self._recover()
                await self._schedule_if_due()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval_seconds)
            except TimeoutError:
                continue
        self._logger.info("worker_stopped")

    async def _recover(self) -> None:
        """Re-dispatch retries and release jobs whose worker never came back."""
        async with self._database.session() as session:
            service = self._service(session)
            report = await service.run_maintenance(
                max_attempts=self._job_max_attempts,
                max_run_age_seconds=self._run_max_age_seconds,
            )
        if report.changed:
            self._logger.info(
                "review_sync_maintenance",
                expired_leases=report.expired_leases,
                requeued_jobs=report.requeued_jobs,
                abandoned_runs=report.abandoned_runs,
            )

    def is_due(self, now: datetime) -> bool:
        """Whether the scheduled moment has passed today.

        The schedule carries minutes, so comparing hours alone would fire a
        00:30 run at midnight — and for hour 0 the old guard never held at all.
        """
        return (now.hour, now.minute) >= (self._sync_hour, self._sync_minute)

    async def _schedule_if_due(self) -> None:
        now = self._now(self._timezone)
        if not self.is_due(now):
            return
        async with self._database.session() as session:
            reviews = ReviewSyncRepository(session)
            if await reviews.run_for_date(now.date()) is not None:
                return
            run = await self._service(session).request("scheduled", now.date())
            self._logger.info("review_sync_scheduled", run_id=str(run.id), status=run.status)

    @staticmethod
    def _service(session) -> ReviewSyncService:
        return ReviewSyncService(session, SellerRepository(session), ReviewSyncRepository(session))
