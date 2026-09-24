import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

from backend.modules.wb_core.application import MirrorService, SellerGoneError
from backend.modules.wb_core.domain import (
    EGRESS_SERVABLE,
    MIRROR_CATALOG,
    MIRROR_CHATS,
    MIRROR_ORDERS,
    MIRROR_REMAINS,
    MIRROR_REVIEWS,
    MIRROR_STOCKS,
    MIRROR_SUPPLIES,
)
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository, SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.shared.heartbeat import touch_heartbeat
from backend.shared.settings import Settings
from backend.storage.pg import Database


class WBCoreWorker:
    """Обход всех активных селлеров реестра: каталог, остатки, отзывы, задания, поставки, чаты, склады WB.

    Подключение к автоматизациям на зеркало не влияет — оно нужно любой из
    них, и собирается один раз. Отметка сбора хранится на паре «селлер + вид»;
    после неудачи повтор через `retry_minutes`, никогда не собранный селлер —
    в очереди сразу.
    """

    def __init__(
        self,
        database: Database,
        mirror: MirrorService,
        settings: Settings,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.mirror = mirror
        self.settings = settings
        self.config = settings.core_mirror
        self.timezone = ZoneInfo(self.config.timezone)
        self._now = now or (lambda: datetime.now(UTC))
        self.logger = structlog.get_logger("wb_core_worker")
        self._stop = asyncio.Event()
        self._pruned_on: date | None = None

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self.logger.info("worker_started")
        while not self._stop.is_set():
            touch_heartbeat()
            try:
                await self.tick()
            except Exception:
                self.logger.exception("wb_core_tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.worker.poll_interval_seconds)
            except TimeoutError:
                continue
        self.logger.info("worker_stopped")

    async def tick(self) -> None:
        # Каталог первым: остатки и отзывы ключуются его размерами и карточками.
        now = self._now()
        await self.collect_due(MIRROR_CATALOG, now)
        await self.collect_due(MIRROR_STOCKS, now)
        await self.collect_due(MIRROR_REVIEWS, now)
        await self.collect_due(MIRROR_ORDERS, now)
        await self.collect_due(MIRROR_SUPPLIES, now)
        await self.collect_due(MIRROR_CHATS, now)
        await self.collect_due(MIRROR_REMAINS, now)
        await self.prune(now)

    async def prune(self, now: datetime) -> int:
        """Задания и события чатов старше срока хранения — раз в сутки."""
        today = now.astimezone(self.timezone).date()
        if self._pruned_on == today:
            return 0
        async with self.database.session() as session:
            mirror = MirrorRepository(session)
            orders = await mirror.prune_orders(now - timedelta(days=self.config.orders_retention_days))
            chats = await mirror.prune_chat_events(now - timedelta(days=self.config.chats_retention_days))
            await session.commit()
        self._pruned_on = today
        if orders or chats:
            self.logger.info("wb_core_mirror_pruned", orders=orders, chat_events=chats)
        return orders + chats

    def due_since(self, kind: str, now: datetime) -> datetime:
        """Момент последнего наступившего сбора этого вида.

        Селлер, собранный позже, уже отработал. До наступления часа сравниваем
        с предыдущим срезом, иначе перезапуск в полночь собрал бы всех заново.
        """
        if kind == MIRROR_ORDERS:
            # Задания — интервалом, а не по часам: свежие перечитываются, пока не лягут в поставку.
            return now - timedelta(minutes=self.config.orders_interval_minutes)
        if kind == MIRROR_CHATS:
            return now - timedelta(minutes=self.config.chats_interval_minutes)
        if kind == MIRROR_REMAINS:
            return now - timedelta(minutes=self.config.remains_interval_minutes)
        local = now.astimezone(self.timezone)
        if kind == MIRROR_STOCKS:
            marks = [(hour, 0) for hour in sorted(self.config.stock_slot_hours)]
        elif kind == MIRROR_CATALOG:
            marks = [(self.config.catalog_hour, self.config.catalog_minute)]
        elif kind == MIRROR_SUPPLIES:
            marks = [(self.config.supplies_hour, self.config.supplies_minute)]
        else:
            marks = [(self.config.reviews_hour, self.config.reviews_minute)]
        candidates = [local.replace(hour=hour, minute=minute, second=0, microsecond=0) for hour, minute in marks]
        passed = [moment for moment in candidates if moment <= local]
        if passed:
            return max(passed).astimezone(UTC)
        return (max(candidates) - timedelta(days=1)).astimezone(UTC)

    async def collect_due(self, kind: str, now: datetime) -> int:
        since = self.due_since(kind, now)
        retry_after = now - timedelta(minutes=self.config.retry_minutes)
        async with self.database.session() as session:
            # Только селлеры с рабочим ключом на шлюзе: остальных шлюз отвергнет,
            # и ошибка зеркала заслонила бы настоящую причину — недоставленный ключ.
            candidates = [
                seller.id
                for seller in await SellerRepository(session).list_sellers()
                if seller.egress_status in EGRESS_SERVABLE
            ]
            mirror = MirrorRepository(session)
            if kind not in (MIRROR_CATALOG, MIRROR_CHATS):
                # Без каталога нечем ключевать остатки и отзывы: у нового селлера
                # первый сбор остатков дал бы нули на весь срез. Чатам каталог не нужен.
                catalog = await mirror.states(MIRROR_CATALOG)
                candidates = [
                    seller_id
                    for seller_id in candidates
                    if seller_id in catalog and catalog[seller_id].collected_at is not None
                ]
            due = await mirror.sellers_due(kind, candidates, since=since, retry_after=retry_after)
        collected = 0
        for seller_id in due:
            if self._stop.is_set():
                break
            # Один селлер — минуты под троттлингом, а healthcheck ждёт heartbeat
            # не реже раза в четверть часа: отмечаемся перед каждым.
            touch_heartbeat()
            if await self.collect(kind, seller_id) is None:
                collected += 1
        return collected

    async def collect(self, kind: str, seller_id: uuid.UUID) -> str | None:
        """Один вид по одному селлеру. Возвращает текст ошибки или None."""
        operation = {
            MIRROR_STOCKS: self.mirror.collect_stocks,
            MIRROR_CATALOG: self.mirror.sync_catalog,
            MIRROR_REVIEWS: self.mirror.collect_reviews,
            MIRROR_ORDERS: self.mirror.collect_orders,
            MIRROR_SUPPLIES: self.mirror.collect_supplies,
            MIRROR_CHATS: self.mirror.collect_chats,
            MIRROR_REMAINS: self.mirror.collect_remains,
        }[kind]
        try:
            await operation(seller_id, now=self._now())
        except SellerGoneError:
            return "seller gone"
        except (WBPermanentError, WBTemporaryError) as error:
            # Состояние уже записано сервисом; здесь только журнал.
            return str(error)
        except Exception as error:
            self.logger.exception("wb_core_collection_crashed", kind=kind, seller_id=str(seller_id))
            text = str(error) or error.__class__.__name__
            async with self.database.session() as session:
                await MirrorRepository(session).mark_failed(seller_id, kind, text)
                await session.commit()
            return text
        return None
