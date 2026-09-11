import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import structlog

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.modules.wb_fbs_stocks.application import CollectionService
from backend.modules.wb_fbs_stocks.infrastructure.postgres import FbsStocksRepository
from backend.modules.wb_fbs_stocks.infrastructure.wb import WBFbsStocksClient
from backend.shared.heartbeat import touch_heartbeat
from backend.shared.settings import Settings
from backend.storage.pg import Database

# Сбор кабинета — запрос на склад, каждый шлюз может держать до пары минут.
# Запрос «в работе» дольше часа живым быть не может: его бросил упавший процесс.
STALE_REFRESH = timedelta(hours=1)


class FbsStocksWorker:
    """Опрос остатков раз в N минут плюс кнопка «Обновить».

    Расписание интервальное, а не суточное: селлер правит кабинет днём и хочет
    видеть результат в тот же день. Отметка последнего сбора хранится на
    кабинете; после неудачи повтор через `retry_minutes`.
    """

    def __init__(self, database: Database, client: WBFbsStocksClient, settings: Settings) -> None:
        self.database = database
        self.client = client
        self.settings = settings
        self.config = settings.fbs_stocks
        self.logger = structlog.get_logger("wb_fbs_stocks_worker")
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
                self.logger.exception("fbs_stocks_tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.worker.poll_interval_seconds)
            except TimeoutError:
                continue
        self.logger.info("worker_stopped")

    async def tick(self) -> None:
        await self.serve_refresh_requests()
        await self.collect_due(datetime.now(UTC))

    async def collect_due(self, now: datetime) -> int:
        since = now - timedelta(minutes=self.config.poll_minutes)
        retry_after = now - timedelta(minutes=self.config.retry_minutes)
        async with self.database.session() as session:
            due = await FbsStocksRepository(session).sellers_due(since, retry_after=retry_after)
            # Архивный кабинет остаётся подключённым до отключения, но ключа его
            # у шлюза уже нет: спрашивать WB от его имени бессмысленно.
            active = {seller.id for seller in await SellerRepository(session).list_sellers()}
        collected = 0
        for seller_id in due:
            if self._stop.is_set():
                break
            if seller_id in active and await self.collect(seller_id) is None:
                collected += 1
        return collected

    async def serve_refresh_requests(self) -> None:
        async with self.database.session() as session:
            stocks = FbsStocksRepository(session)
            abandoned = await stocks.abandon_stale_refreshes(datetime.now(UTC) - STALE_REFRESH)
            requests = [(request.id, request.seller_id) for request in await stocks.claim_refreshes()]
            await session.commit()
        if abandoned:
            self.logger.warning("fbs_stocks_refresh_abandoned", count=abandoned)
        for request_id, seller_id in requests:
            error = await self.collect(seller_id)
            async with self.database.session() as session:
                await FbsStocksRepository(session).finish_refresh(request_id, error)
                await session.commit()
            self.logger.info("fbs_stocks_refresh_served", seller_id=str(seller_id), failed=bool(error))

    async def collect(self, seller_id: uuid.UUID) -> str | None:
        """Сбор одного кабинета. Возвращает текст ошибки или None, если прошёл."""
        async with self.database.session() as session:
            await FbsStocksRepository(session).record_attempt(seller_id)
            await session.commit()
        try:
            async with self.database.session() as session:
                result = await CollectionService(session, FbsStocksRepository(session), self.client).collect(seller_id)
        except (WBPermanentError, WBTemporaryError) as error:
            self.logger.warning("fbs_stocks_collection_failed", seller_id=str(seller_id), error=str(error))
            await self._fail(seller_id, str(error))
            return str(error)
        except Exception as error:
            self.logger.exception("fbs_stocks_collection_crashed", seller_id=str(seller_id))
            await self._fail(seller_id, str(error) or error.__class__.__name__)
            return str(error) or error.__class__.__name__
        self.logger.info(
            "fbs_stocks_collected",
            seller_id=str(seller_id),
            warehouses=result.warehouses,
            polled=result.polled,
            barcodes=result.barcodes,
            skipped=result.skipped,
        )
        return None

    async def _fail(self, seller_id: uuid.UUID, error: str) -> None:
        async with self.database.session() as session:
            await FbsStocksRepository(session).fail_collection(seller_id, error)
            await session.commit()
