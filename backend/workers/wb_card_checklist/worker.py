import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

from backend.modules.wb_card_checklist.application import CollectionService
from backend.modules.wb_card_checklist.infrastructure.postgres import ChecklistRepository
from backend.modules.wb_card_checklist.infrastructure.wb import WBCardClient, WBPricesClient
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.shared.heartbeat import touch_heartbeat
from backend.shared.settings import Settings
from backend.storage.pg import Database

# Сбор одного селлера — это страницы карточек, цены и справочники, каждый
# запрос шлюз может держать до пары минут. Запрос «в работе» дольше часа
# живым быть не может: его бросил упавший процесс.
STALE_REFRESH = timedelta(hours=1)


class CardChecklistWorker:
    """Расписание чек-листа: суточный сбор карточек и цен плюс кнопка «Обновить».

    Слоты не резервируются журналом: сбор переписывает данные селлера целиком,
    и повтор после перезапуска ничего не портит. Хватает отметки последнего
    успешного сбора и последней попытки на самом селлере.
    """

    def __init__(
        self,
        database: Database,
        cards: WBCardClient,
        prices: WBPricesClient,
        settings: Settings,
        now: Callable[[ZoneInfo], datetime] | None = None,
    ) -> None:
        self.database = database
        self.cards = cards
        self.prices = prices
        self.settings = settings
        self.config = settings.card_checklist
        self.timezone = ZoneInfo(self.config.timezone)
        self._now = now or (lambda timezone: datetime.now(timezone))
        self.logger = structlog.get_logger("wb_card_checklist_worker")
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
                self.logger.exception("checklist_tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.worker.poll_interval_seconds)
            except TimeoutError:
                continue
        self.logger.info("worker_stopped")

    async def tick(self) -> None:
        await self.serve_refresh_requests()
        await self.collect_due(self._now(self.timezone))

    def due_since(self, now: datetime) -> datetime:
        """Момент последнего наступившего сбора.

        Селлер, собранный позже этого момента, сегодня уже отработал. До
        наступления часа сравниваем со вчерашним, иначе перезапуск в полночь
        собрал бы всех заново.
        """
        local = now.astimezone(self.timezone)
        scheduled = local.replace(
            hour=self.config.collect_hour, minute=self.config.collect_minute, second=0, microsecond=0
        )
        if scheduled > local:
            scheduled -= timedelta(days=1)
        return scheduled.astimezone(UTC)

    async def collect_due(self, now: datetime) -> int:
        since = self.due_since(now)
        retry_after = now.astimezone(UTC) - timedelta(minutes=self.config.retry_minutes)
        async with self.database.session() as session:
            due = await ChecklistRepository(session).sellers_due(since, retry_after=retry_after)
            # Архивный селлер остаётся подключённым до отключения, но ключа его
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
        """Собрать вне расписания для тех, кто нажал кнопку."""
        async with self.database.session() as session:
            checklist = ChecklistRepository(session)
            abandoned = await checklist.abandon_stale_refreshes(datetime.now(UTC) - STALE_REFRESH)
            requests = [(request.id, request.seller_id) for request in await checklist.claim_refreshes()]
            await session.commit()
        if abandoned:
            self.logger.warning("checklist_refresh_abandoned", count=abandoned)
        for request_id, seller_id in requests:
            error = await self.collect(seller_id)
            async with self.database.session() as session:
                await ChecklistRepository(session).finish_refresh(request_id, error)
                await session.commit()
            self.logger.info("checklist_refresh_served", seller_id=str(seller_id), failed=bool(error))

    async def collect(self, seller_id: uuid.UUID) -> str | None:
        """One seller's collection. Returns the error text, or None when it went through."""
        # Попытка отмечается до сети: упавший процесс не должен превращаться в
        # кабинет, который спрашивают снова и снова.
        async with self.database.session() as session:
            await ChecklistRepository(session).record_attempt(seller_id)
            await session.commit()
        try:
            async with self.database.session() as session:
                result = await CollectionService(
                    session,
                    ChecklistRepository(session),
                    self.cards,
                    self.prices,
                    subject_ttl=timedelta(hours=self.config.subject_ttl_hours),
                ).collect(seller_id)
        except (WBPermanentError, WBTemporaryError) as error:
            self.logger.warning("checklist_collection_failed", seller_id=str(seller_id), error=str(error))
            await self._fail(seller_id, str(error))
            return str(error)
        except Exception as error:
            self.logger.exception("checklist_collection_crashed", seller_id=str(seller_id))
            await self._fail(seller_id, str(error) or error.__class__.__name__)
            return str(error) or error.__class__.__name__
        self.logger.info(
            "checklist_collected",
            seller_id=str(seller_id),
            cards=result.cards,
            prices=result.prices,
            subjects=result.subjects,
            warning=result.warning,
            skipped=result.skipped,
        )
        return None

    async def _fail(self, seller_id: uuid.UUID, error: str) -> None:
        async with self.database.session() as session:
            await ChecklistRepository(session).fail_collection(seller_id, error)
            await session.commit()
