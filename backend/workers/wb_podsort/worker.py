import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.domain import EGRESS_SERVABLE
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.modules.wb_podsort.application import CollectionService
from backend.modules.wb_podsort.infrastructure.postgres import PodsortRepository
from backend.modules.wb_podsort.infrastructure.wb import WBPodsortStatisticsClient
from backend.shared.heartbeat import touch_heartbeat
from backend.shared.settings import Settings
from backend.storage.pg import Database


class PodsortWorker:
    """Догрузка суточных итогов заказов по подключённым кабинетам.

    Каждый проход — по кабинетам по очереди, у каждого не больше
    `days_per_run` суток: запрос «Статистики» раз в минуту на ключ, и первые
    три месяца кабинета не должны держать остальных. Кабинет без долгов
    пропускается без запроса к WB; после неудачи — пауза `retry_minutes`.
    """

    def __init__(
        self,
        database: Database,
        client: WBPodsortStatisticsClient,
        settings: Settings,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.client = client
        self.settings = settings
        self.config = settings.podsort
        self.timezone = ZoneInfo(self.config.timezone)
        self._now = now or (lambda: datetime.now(UTC))
        self.logger = structlog.get_logger("wb_podsort_worker")
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self.logger.info("worker_started")
        while not self._stop.is_set():
            touch_heartbeat()
            try:
                await self.tick()
            except Exception:
                self.logger.exception("wb_podsort_tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.worker.poll_interval_seconds)
            except TimeoutError:
                continue
        self.logger.info("worker_stopped")

    async def tick(self) -> int:
        now = self._now()
        retry_after = now - timedelta(minutes=self.config.retry_minutes)
        async with self.database.session() as session:
            tracked = await PodsortRepository(session).tracked_rows()
            # Архивный кабинет или кабинет без ключа на шлюзе WB отвергнет, и
            # ошибка подсорта заслонила бы настоящую причину.
            servable = {
                seller.id
                for seller in await SellerRepository(session).list_sellers()
                if seller.egress_status in EGRESS_SERVABLE
            }
        collected = 0
        for row in tracked:
            if self._stop.is_set():
                break
            if row.seller_id not in servable:
                continue
            if row.collection_error and row.attempted_at is not None and row.attempted_at > retry_after:
                continue
            touch_heartbeat()
            if await self.collect(row.seller_id, now) is None:
                collected += 1
        return collected

    def service(self, session: AsyncSession) -> CollectionService:
        return CollectionService(
            session,
            PodsortRepository(session),
            self.client,
            timezone=self.timezone,
            history_days=self.config.history_days,
            days_per_run=self.config.days_per_run,
            settle_hours=self.config.settle_hours,
            refresh_minutes=self.config.refresh_minutes,
            request_interval_seconds=self.config.request_interval_seconds,
        )

    async def collect(self, seller_id: uuid.UUID, now: datetime) -> str | None:
        """Один кабинет. None — прошёл (или делать было нечего), иначе текст ошибки."""
        async with self.database.session() as session:
            service = self.service(session)
            if not await service.pending(seller_id, now=now):
                return None
            await PodsortRepository(session).record_attempt(seller_id, now=now)
            await session.commit()
        try:
            async with self.database.session() as session:
                result = await self.service(session).collect(seller_id, now=now)
        except (WBPermanentError, WBTemporaryError) as error:
            self.logger.warning("wb_podsort_collection_failed", seller_id=str(seller_id), error=str(error))
            await self._fail(seller_id, str(error))
            return str(error)
        except Exception as error:
            self.logger.exception("wb_podsort_collection_crashed", seller_id=str(seller_id))
            text = str(error) or error.__class__.__name__
            await self._fail(seller_id, text)
            return text
        self.logger.info(
            "wb_podsort_collected",
            seller_id=str(seller_id),
            days_loaded=result.days_loaded,
            orders=result.orders,
            remaining=result.remaining,
            skipped=result.skipped,
        )
        return None

    async def _fail(self, seller_id: uuid.UUID, error: str) -> None:
        async with self.database.session() as session:
            await PodsortRepository(session).fail_collection(seller_id, error)
            await session.commit()
