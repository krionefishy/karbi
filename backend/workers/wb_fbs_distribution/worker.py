import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.modules.wb_fbs_distribution.application import (
    MirrorService,
    SnapshotRejected,
    SnapshotService,
    StockSnapshotSource,
)
from backend.modules.wb_fbs_distribution.infrastructure.postgres import FbsDistributionRepository
from backend.modules.wb_fbs_distribution.infrastructure.wb import WBFbsMarketplaceClient
from backend.shared.heartbeat import touch_heartbeat
from backend.shared.settings import Settings
from backend.storage.pg import Database


class FbsDistributionWorker:
    """Расписание автоматизации: пока только суточная сверка складов кабинета.

    Слоты не резервируются журналом, как в оборачиваемости: сверка приводит
    зеркало к тому, что вернул WB, и повтор после перезапуска ничего не портит.
    Достаточно времени последней успешной сверки на самом кабинете.
    """

    def __init__(
        self,
        database: Database,
        marketplace: WBFbsMarketplaceClient,
        stock_source: StockSnapshotSource,
        settings: Settings,
        now: Callable[[ZoneInfo], datetime] | None = None,
    ) -> None:
        self.database = database
        self.marketplace = marketplace
        self.stock_source = stock_source
        self.settings = settings
        self.config = settings.fbs_distribution
        self.timezone = ZoneInfo(self.config.timezone)
        self._now = now or (lambda timezone: datetime.now(timezone))
        self.logger = structlog.get_logger("wb_fbs_distribution_worker")
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
                self.logger.exception("fbs_tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.worker.poll_interval_seconds)
            except TimeoutError:
                continue
        self.logger.info("worker_stopped")

    async def tick(self) -> None:
        await self.poll_stock_source()
        await self.sync_mirror(self._now(self.timezone))

    async def poll_stock_source(self) -> bool:
        """Забрать снимок остатков у источника, если тот отдаёт данные сам.

        Заглушка отвечает None, и шаг молча пропускается. Когда за портом
        появится «тянущий» адаптер 1С, он заработает без правок воркера.
        Пуш-обмен идёт мимо этого шага, через HTTP-эндпоинт.
        """
        snapshot = await self.stock_source.fetch()
        if snapshot is None:
            return False
        async with self.database.session() as session:
            distribution = FbsDistributionRepository(session)
            latest = await distribution.latest_snapshot()
            # Источник может отдавать один и тот же снимок каждый оборот;
            # без отсечки журнал наполнялся бы повторами каждые полминуты.
            if latest is not None and latest.generated_at >= snapshot.generated_at:
                return False
            service = SnapshotService(
                session,
                distribution,
                max_age_minutes=self.config.snapshot_max_age_minutes,
            )
            try:
                await service.accept(snapshot, source=self.stock_source.source_id)
            except SnapshotRejected as error:
                self.logger.warning("fbs_snapshot_rejected", source=self.stock_source.source_id, error=str(error))
                return False
        self.logger.info("fbs_snapshot_accepted", source=self.stock_source.source_id, lines=len(snapshot.lines))
        return True

    def due_since(self, now: datetime) -> datetime:
        """Момент последней наступившей сверки.

        Кабинет, сверенный позже этого момента, сегодня уже отработал. До
        наступления часа сравниваем со вчерашним, иначе перезапуск в полночь
        сверил бы всех заново.
        """
        local = now.astimezone(self.timezone)
        scheduled = local.replace(
            hour=self.config.mirror_hour, minute=self.config.mirror_minute, second=0, microsecond=0
        )
        if scheduled > local:
            scheduled -= timedelta(days=1)
        return scheduled.astimezone(UTC)

    async def sync_mirror(self, now: datetime) -> int:
        since = self.due_since(now)
        retry_after = now.astimezone(UTC) - timedelta(minutes=self.config.mirror_retry_minutes)
        async with self.database.session() as session:
            due = await FbsDistributionRepository(session).sellers_due_for_sync(since, retry_after=retry_after)
        synced = 0
        for seller_id in due:
            if self._stop.is_set():
                break
            if await self._sync_seller(seller_id):
                synced += 1
        return synced

    async def _sync_seller(self, seller_id: uuid.UUID) -> bool:
        # Попытка отмечается до сети: упавший процесс не должен превращаться в
        # кабинет, который спрашивают снова и снова.
        async with self.database.session() as session:
            await FbsDistributionRepository(session).record_sync_attempt(seller_id)
            await session.commit()
        async with self.database.session() as session:
            service = MirrorService(
                session,
                SellerRepository(session),
                FbsDistributionRepository(session),
                self.marketplace,
            )
            try:
                result = await service.sync_seller(seller_id)
            except WBPermanentError as error:
                # Ключ без прав или отозван: повтор через минуту ничего не
                # изменит, поэтому кабинет ждёт следующего срока.
                self.logger.warning("fbs_mirror_rejected", seller_id=str(seller_id), error=str(error))
                return False
            except WBTemporaryError as error:
                self.logger.info("fbs_mirror_deferred", seller_id=str(seller_id), error=str(error))
                return False
        self.logger.info(
            "fbs_mirror_synced",
            seller_id=str(seller_id),
            offices=result.offices,
            warehouses=result.warehouses,
        )
        return True
