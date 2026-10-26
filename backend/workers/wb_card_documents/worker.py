import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotRegistry
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.wb_card_documents.application import (
    CardDocumentsPublisher,
    CardDocumentsSource,
    CollectionService,
    MirrorStockSource,
    NotificationService,
)
from backend.modules.wb_card_documents.infrastructure.postgres import CardDocumentsRepository
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.shared.heartbeat import touch_heartbeat
from backend.shared.settings import Settings
from backend.shared.worker import PollingWorker
from backend.storage.pg import Database

STALE_REFRESH = timedelta(hours=1)


class CardDocumentsWorker(PollingWorker):
    """Документы карточек: изменённые карточки раз в `interval_minutes`, полный обход раз в
    `full_interval_hours`, кнопка «Обновить» (всегда полный обход) и дайджест.

    Попытка отмечается до сети, после неудачи повтор через `retry_minutes`, никогда не
    собранный кабинет идёт сразу. В часы `skip_hours` плановый сбор не начинается — это
    окна каталога и чек-листа, бюджет «Контента» у них общий. Дайджест проверяется
    каждый проход: ему сеть WB не нужна. Сбои наблюдения в Telegram не уходят — их
    показывает страница автоматизации.
    """

    tick_failed_event = "card_documents_tick_failed"

    def __init__(
        self,
        database: Database,
        source: CardDocumentsSource,
        settings: Settings,
        *,
        now: Callable[[ZoneInfo], datetime] | None = None,
    ) -> None:
        super().__init__(
            logger_name="wb_card_documents_worker", poll_interval_seconds=settings.worker.poll_interval_seconds
        )
        self.database = database
        self.source = source
        self.settings = settings
        self.config = settings.card_documents
        self.timezone = ZoneInfo(self.config.timezone)
        self._now = now or (lambda timezone: datetime.now(timezone))

    async def tick(self) -> None:
        await self.serve_refresh_requests()
        now = self._now(self.timezone)
        await self.collect_due(now)
        await self.watch_all(self._now(self.timezone))

    def due_since(self, now: datetime) -> datetime:
        return now.astimezone(UTC) - timedelta(minutes=self.config.interval_minutes)

    def quiet(self, now: datetime) -> bool:
        return now.astimezone(self.timezone).hour in self.config.skip_hours

    async def _active_tracked(self) -> list[uuid.UUID]:
        async with self.database.session() as session:
            tracked = await CardDocumentsRepository(session).tracked_seller_ids()
            active = {seller.id for seller in await SellerRepository(session).list_sellers()}
        return sorted(tracked & active, key=str)

    async def collect_due(self, now: datetime) -> int:
        if self.quiet(now):
            return 0
        retry_after = now.astimezone(UTC) - timedelta(minutes=self.config.retry_minutes)
        async with self.database.session() as session:
            due = await CardDocumentsRepository(session).sellers_due(self.due_since(now), retry_after=retry_after)
        active = set(await self._active_tracked())
        collected = 0
        for seller_id in due:
            if self._stop.is_set():
                break
            touch_heartbeat()
            if seller_id in active and await self.collect(seller_id) is None:
                collected += 1
        return collected

    async def watch_all(self, now: datetime) -> int:
        sent = 0
        for seller_id in await self._active_tracked():
            if self._stop.is_set():
                break
            touch_heartbeat()
            try:
                async with self.database.session() as session:
                    report = await self.notifications(session).watch(seller_id, now=now.astimezone(UTC))
            except Exception:
                self.logger.exception("card_documents_watch_failed", seller_id=str(seller_id))
                continue
            if report.digest:
                sent += 1
                self.logger.info("card_documents_digest_sent", seller_id=str(seller_id))
        return sent

    async def serve_refresh_requests(self) -> None:
        async with self.database.session() as session:
            documents = CardDocumentsRepository(session)
            abandoned = await documents.abandon_stale_refreshes(datetime.now(UTC) - STALE_REFRESH)
            requests = [(request.id, request.seller_id) for request in await documents.claim_refreshes()]
            await session.commit()
        if abandoned:
            self.logger.warning("card_documents_refresh_abandoned", count=abandoned)
        active = set(await self._active_tracked()) if requests else set()
        for request_id, seller_id in requests:
            touch_heartbeat()
            error = await self.collect(seller_id, full=True) if seller_id in active else "Кабинет отключён или в архиве"
            async with self.database.session() as session:
                await CardDocumentsRepository(session).finish_refresh(request_id, error)
                await session.commit()
            self.logger.info("card_documents_refresh_served", seller_id=str(seller_id), failed=bool(error))

    async def collect(self, seller_id: uuid.UUID, *, full: bool = False) -> str | None:
        """Сбор одного кабинета. Возвращает текст ошибки или None, если прошёл.

        По расписанию вид обхода решает сбор (изменённые или раз в сутки полный);
        `full=True` — кнопка «Обновить»: человек ждёт весь каталог, а не только изменения.
        """
        async with self.database.session() as session:
            await CardDocumentsRepository(session).record_attempt(seller_id)
            await session.commit()
        try:
            async with self.database.session() as session:
                result = await CollectionService(
                    session,
                    SellerRepository(session),
                    CardDocumentsRepository(session),
                    self.source,
                    self.publisher(session),
                    self.stocks(session),
                    timezone=self.timezone,
                    thresholds=self.config.expiry_thresholds,
                    digest_time=f"{self.config.digest_hour}:{self.config.digest_minute:02d}",
                    full_interval=timedelta(hours=self.config.full_interval_hours),
                    cursor_margin=timedelta(seconds=self.config.cursor_margin_seconds),
                    min_stock=self.config.min_stock_for_alerts,
                ).collect(seller_id, full=full)
        except WBPermanentError as error:
            self.logger.warning("card_documents_collection_failed", seller_id=str(seller_id), error=str(error))
            await self._fail(seller_id, str(error), permanent=True)
            return str(error)
        except WBTemporaryError as error:
            self.logger.warning("card_documents_collection_failed", seller_id=str(seller_id), error=str(error))
            await self._fail(seller_id, str(error))
            return str(error)
        except Exception as error:
            self.logger.exception("card_documents_collection_crashed", seller_id=str(seller_id))
            await self._fail(seller_id, str(error) or error.__class__.__name__)
            return str(error) or error.__class__.__name__
        self.logger.info(
            "card_documents_collected",
            seller_id=str(seller_id),
            cards=result.cards,
            problems=result.problems,
            alerts=result.alerts,
            closed=result.closed,
            baseline=result.baseline,
            summary_sent=result.summary_sent,
            full=result.full,
            skipped=result.skipped,
        )
        return None

    def publisher(self, session: AsyncSession) -> CardDocumentsPublisher:
        return CardDocumentsPublisher(
            session,
            BotRegistry(session, NotificationRepository(session)),
            bot_code=self.config.notification_bot,
        )

    def stocks(self, session: AsyncSession) -> MirrorStockSource:
        """Остаток для фильтра уведомлений — из зеркала wb_core, сами в WB за ним не ходим."""
        return MirrorStockSource(session, fresh_hours=self.config.stock_fresh_hours)

    def notifications(self, session: AsyncSession) -> NotificationService:
        return NotificationService(
            session,
            SellerRepository(session),
            CardDocumentsRepository(session),
            self.publisher(session),
            self.stocks(session),
            timezone=self.timezone,
            digest_hour=self.config.digest_hour,
            digest_minute=self.config.digest_minute,
            stale_hours=self.config.stale_hours,
            min_stock=self.config.min_stock_for_alerts,
        )

    async def _fail(self, seller_id: uuid.UUID, error: str, *, permanent: bool = False) -> None:
        """Ошибка остаётся на кабинете; страница показывает её баннером, пока следующий сбор не пройдёт."""
        async with self.database.session() as session:
            await CardDocumentsRepository(session).fail_collection(seller_id, error, permanent=permanent)
            await session.commit()
