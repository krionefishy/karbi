import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.modules.wb_fbs_penalties.application import CollectionService
from backend.modules.wb_fbs_penalties.infrastructure.postgres import PenaltiesRepository
from backend.modules.wb_fbs_penalties.infrastructure.wb import WBFinanceClient
from backend.shared.heartbeat import touch_heartbeat
from backend.shared.settings import Settings
from backend.storage.pg import Database

# Проход — до шести отчётов по минуте на страницу (лимит WB). Запрос «в работе»
# дольше часа живым быть не может: его бросил упавший процесс.
STALE_REFRESH = timedelta(hours=1)


class PenaltiesWorker:
    """Сбор фин. отчёта каждые `poll_hours` плюс кнопка «Обновить».

    Отчёты суточные и появляются на следующий день в неизвестный час, поэтому
    список спрашивается несколько раз в сутки (один запрос), а детализация
    читается только у новых отчётов. После неудачи повтор через `retry_minutes`.
    """

    def __init__(
        self,
        database: Database,
        client: WBFinanceClient,
        settings: Settings,
        *,
        now: Callable[[ZoneInfo], datetime] | None = None,
    ) -> None:
        self.database = database
        self.client = client
        self.settings = settings
        self.config = settings.fbs_penalties
        self.timezone = ZoneInfo(self.config.timezone)
        self._now = now or (lambda tz: datetime.now(tz))
        self.logger = structlog.get_logger("wb_fbs_penalties_worker")
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
                self.logger.exception("fbs_penalties_tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.worker.poll_interval_seconds)
            except TimeoutError:
                continue
        self.logger.info("worker_stopped")

    async def tick(self) -> None:
        await self.serve_refresh_requests()
        await self.collect_due(self._now(self.timezone))

    def due_since(self, now: datetime) -> datetime:
        """Кабинет пора собирать, если последний удачный сбор старше `poll_hours`."""
        return now.astimezone(UTC) - timedelta(hours=self.config.poll_hours)

    async def collect_due(self, now: datetime) -> int:
        since = self.due_since(now)
        retry_after = now.astimezone(UTC) - timedelta(minutes=self.config.retry_minutes)
        async with self.database.session() as session:
            due = await PenaltiesRepository(session).sellers_due(since, retry_after=retry_after)
            # Архивный кабинет остаётся подключённым до отключения, но ключа его
            # у шлюза уже нет: спрашивать WB от его имени бессмысленно.
            active = {seller.id for seller in await SellerRepository(session).list_sellers()}
        collected = 0
        for seller_id in due:
            if self._stop.is_set():
                break
            touch_heartbeat()
            if seller_id in active and await self.collect(seller_id) is None:
                collected += 1
        return collected

    async def serve_refresh_requests(self) -> None:
        async with self.database.session() as session:
            penalties = PenaltiesRepository(session)
            abandoned = await penalties.abandon_stale_refreshes(datetime.now(UTC) - STALE_REFRESH)
            requests = [(request.id, request.seller_id) for request in await penalties.claim_refreshes()]
            await session.commit()
        if abandoned:
            self.logger.warning("fbs_penalties_refresh_abandoned", count=abandoned)
        for request_id, seller_id in requests:
            touch_heartbeat()
            error = await self.collect(seller_id)
            async with self.database.session() as session:
                await PenaltiesRepository(session).finish_refresh(request_id, error)
                await session.commit()
            self.logger.info("fbs_penalties_refresh_served", seller_id=str(seller_id), failed=bool(error))

    async def collect(self, seller_id: uuid.UUID) -> str | None:
        """Сбор одного кабинета. Возвращает текст ошибки или None, если прошёл."""
        async with self.database.session() as session:
            await PenaltiesRepository(session).record_attempt(seller_id)
            await session.commit()
        try:
            async with self.database.session() as session:
                service = CollectionService(
                    session,
                    PenaltiesRepository(session),
                    self.client,
                    window_days=self.config.report_window_days,
                    backfill_days=self.config.report_backfill_days,
                    reports_per_run=self.config.reports_per_run,
                )
                result = await service.collect(seller_id)
        except (WBPermanentError, WBTemporaryError) as error:
            self.logger.warning("fbs_penalties_collection_failed", seller_id=str(seller_id), error=str(error))
            await self._fail(seller_id, str(error))
            return str(error)
        except Exception as error:
            self.logger.exception("fbs_penalties_collection_crashed", seller_id=str(seller_id))
            await self._fail(seller_id, str(error) or error.__class__.__name__)
            return str(error) or error.__class__.__name__
        self.logger.info(
            "fbs_penalties_collected",
            seller_id=str(seller_id),
            reports_seen=result.reports_seen,
            reports_loaded=result.reports_loaded,
            rows_seen=result.rows_seen,
            rows_kept=result.rows_kept,
            more=result.more,
            skipped=result.skipped,
        )
        return None

    async def _fail(self, seller_id: uuid.UUID, error: str) -> None:
        async with self.database.session() as session:
            await PenaltiesRepository(session).fail_collection(seller_id, error)
            await session.commit()
