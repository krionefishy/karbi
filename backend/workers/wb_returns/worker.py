import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotRegistry
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.modules.wb_returns.application import CollectionService, ExtensionService, NotificationService
from backend.modules.wb_returns.infrastructure.postgres import ReturnsRepository
from backend.modules.wb_returns.infrastructure.wb import WBClaimsClient, WBReturnsReportClient
from backend.shared.heartbeat import touch_heartbeat
from backend.shared.settings import Settings
from backend.storage.pg import Database

# Запрос «в работе» дольше получаса живым быть не может: его бросил упавший процесс.
STALE_REFRESH = timedelta(minutes=30)


class ReturnsWorker:
    """Сбор возвратов и заявок каждые `poll_minutes`, уведомления после каждого сбора и по расписанию.

    Отчёт отдаётся целиком за окно, заявки страницами; оба лимита держит шлюз.
    После неудачи повтор через `retry_minutes`. Уведомления по расписанию
    (дайджест, напоминания, сроки заявок) проверяются каждый проход по всем
    подключённым кабинетам — им сбор не нужен.
    """

    def __init__(
        self,
        database: Database,
        report_client: WBReturnsReportClient,
        claims_client: WBClaimsClient,
        settings: Settings,
        *,
        now: Callable[[ZoneInfo], datetime] | None = None,
    ) -> None:
        self.database = database
        self.report_client = report_client
        self.claims_client = claims_client
        self.settings = settings
        self.config = settings.returns
        self.timezone = ZoneInfo(self.config.timezone)
        self._now = now or (lambda tz: datetime.now(tz))
        self.logger = structlog.get_logger("wb_returns_worker")
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
                self.logger.exception("returns_tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.worker.poll_interval_seconds)
            except TimeoutError:
                continue
        self.logger.info("worker_stopped")

    async def tick(self) -> None:
        now = self._now(self.timezone)
        await self.serve_refresh_requests()
        await self.collect_due(now)
        await self.notify_all(now)

    def due_since(self, now: datetime) -> datetime:
        return now.astimezone(UTC) - timedelta(minutes=self.config.poll_minutes)

    async def _active_tracked(self) -> list[uuid.UUID]:
        async with self.database.session() as session:
            tracked = await ReturnsRepository(session).tracked_seller_ids()
            # Архивный кабинет остаётся подключённым до отключения, но ключа его
            # у шлюза уже нет: спрашивать WB от его имени бессмысленно.
            active = {seller.id for seller in await SellerRepository(session).list_sellers()}
        return sorted(tracked & active, key=str)

    async def collect_due(self, now: datetime) -> int:
        since = self.due_since(now)
        retry_after = now.astimezone(UTC) - timedelta(minutes=self.config.retry_minutes)
        async with self.database.session() as session:
            due = await ReturnsRepository(session).sellers_due(since, retry_after=retry_after)
        active = set(await self._active_tracked())
        collected = 0
        for seller_id in due:
            if self._stop.is_set():
                break
            touch_heartbeat()
            if seller_id in active and await self.collect(seller_id) is None:
                collected += 1
        return collected

    async def notify_all(self, now: datetime) -> int:
        sent = 0
        for seller_id in await self._active_tracked():
            if self._stop.is_set():
                break
            touch_heartbeat()
            sent += await self.notify(seller_id, now)
        return sent

    async def notify(self, seller_id: uuid.UUID, now: datetime) -> int:
        try:
            async with self.database.session() as session:
                service = NotificationService(
                    session,
                    SellerRepository(session),
                    ReturnsRepository(session),
                    BotRegistry(session, NotificationRepository(session)),
                    extension_service(session, self.settings),
                    bot_code=self.config.notification_bot,
                    timezone=self.timezone,
                    digest_hour=self.config.digest_hour,
                    digest_minute=self.config.digest_minute,
                    code_alert_hour=self.config.code_alert_hour,
                    install_silent_hours=self.config.install_silent_hours,
                )
                report = await service.notify(seller_id, now=now.astimezone(UTC))
        except Exception:
            self.logger.exception("returns_notify_failed", seller_id=str(seller_id))
            return 0
        if report.sent:
            self.logger.info(
                "returns_notified",
                seller_id=str(seller_id),
                ready=report.ready,
                transit=report.transit,
                reminders=report.reminders,
                claim_deadlines=report.claim_deadlines,
            )
        return report.sent

    async def serve_refresh_requests(self) -> None:
        async with self.database.session() as session:
            returns = ReturnsRepository(session)
            abandoned = await returns.abandon_stale_refreshes(datetime.now(UTC) - STALE_REFRESH)
            requests = [(request.id, request.seller_id) for request in await returns.claim_refreshes()]
            await session.commit()
        if abandoned:
            self.logger.warning("returns_refresh_abandoned", count=abandoned)
        active = set(await self._active_tracked()) if requests else set()
        for request_id, seller_id in requests:
            touch_heartbeat()
            # Кабинет могли заархивировать после нажатия: ключа у шлюза уже нет.
            error = await self.collect(seller_id) if seller_id in active else "Кабинет отключён или в архиве"
            async with self.database.session() as session:
                await ReturnsRepository(session).finish_refresh(request_id, error)
                await session.commit()
            self.logger.info("returns_refresh_served", seller_id=str(seller_id), failed=bool(error))

    async def collect(self, seller_id: uuid.UUID) -> str | None:
        """Сбор одного кабинета. Возвращает текст ошибки или None, если прошёл."""
        async with self.database.session() as session:
            await ReturnsRepository(session).record_attempt(seller_id)
            await session.commit()
        try:
            async with self.database.session() as session:
                service = CollectionService(
                    session,
                    ReturnsRepository(session),
                    self.report_client,
                    self.claims_client,
                    window_days=self.config.report_window_days,
                    backfill_days=self.config.report_backfill_days,
                    claims_archive_hours=self.config.claims_archive_hours,
                )
                result = await service.collect(seller_id)
        except (WBPermanentError, WBTemporaryError) as error:
            self.logger.warning("returns_collection_failed", seller_id=str(seller_id), error=str(error))
            await self._fail(seller_id, str(error))
            return str(error)
        except Exception as error:
            self.logger.exception("returns_collection_crashed", seller_id=str(seller_id))
            await self._fail(seller_id, str(error) or error.__class__.__name__)
            return str(error) or error.__class__.__name__
        self.logger.info(
            "returns_collected",
            seller_id=str(seller_id),
            returns_seen=result.returns_seen,
            changes=len(result.changes),
            claims_seen=result.claims_seen,
            new_claims=len(result.new_claims),
            skipped=result.skipped,
        )
        return None

    async def _fail(self, seller_id: uuid.UUID, error: str) -> None:
        async with self.database.session() as session:
            await ReturnsRepository(session).fail_collection(seller_id, error)
            await session.commit()


def extension_service(session: AsyncSession, settings: Settings) -> ExtensionService:
    """Сервис расширения с настройками процесса: те же, что у API, но без контейнера DI."""
    returns = settings.returns
    return ExtensionService(
        session,
        SellerRepository(session),
        ReturnsRepository(session),
        bot_code=returns.notification_bot,
        timezone=ZoneInfo(returns.timezone),
        public_base_url=returns.public_base_url,
        download_path=returns.extension_download_path,
        pairing_ttl_minutes=returns.pairing_ttl_minutes,
        qr_secret=settings.auth.jwt_secret,
    )
