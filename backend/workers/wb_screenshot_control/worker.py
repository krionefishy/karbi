from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.modules.wb_screenshot_control.application.outcomes import expire_outcomes
from backend.modules.wb_screenshot_control.application.purge import purge_expired
from backend.modules.wb_screenshot_control.infrastructure.wiring import ScreenshotControlServices
from backend.shared.settings import Settings
from backend.shared.worker import PollingWorker
from backend.storage.pg import Database

EXPIRE_EVERY = timedelta(minutes=1)
RECONCILE_EVERY = timedelta(hours=1)


class ScreenshotControlWorker(PollingWorker):
    """Контроль скриншотов ДБС: фоновые шаги процесса; распознавание и решения — консьюмеры Kafka.

    Каждую минуту — истечение исхода; раз в час — сверка хранилища (зависшие выгрузки, пропавшие
    объекты, сироты staging); раз в сутки в `purge_hour:purge_minute` по Москве — чистка по сроку.
    Шаги независимы: сбой одного не мешает остальным. Без S3 сверка только смотрит на staging,
    чистка не запускается.
    """

    tick_failed_event = "screenshot_control_tick_failed"

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        services: ScreenshotControlServices | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(
            logger_name="wb_screenshot_control_worker",
            poll_interval_seconds=settings.screenshot_control.poll_seconds,
        )
        self.database = database
        self.settings = settings
        self.config = settings.screenshot_control
        self.timezone = ZoneInfo(self.config.timezone)
        self.services = services or ScreenshotControlServices(database, settings)
        self._now = now or (lambda: datetime.now(UTC))
        self.expired_at: datetime | None = None
        self.reconciled_at: datetime | None = None
        self.purged_on: date | None = None

    async def tick(self) -> None:
        if self.config.mode == "off":
            return
        now = self._now()
        if self.expired_at is None or now - self.expired_at >= EXPIRE_EVERY:
            self.expired_at = now
            await self._step("expire", lambda: expire_outcomes(self.database, self.config, now))
        if self.reconciled_at is None or now - self.reconciled_at >= RECONCILE_EVERY:
            self.reconciled_at = now
            await self._step("reconcile", lambda: self.services.reconciler().run(now))
        if self.purge_due(now):
            self.purged_on = now.astimezone(self.timezone).date()
            await self._step("purge", lambda: purge_expired(self.database, self.config, now))

    def purge_due(self, now: datetime) -> bool:
        if self.services.blobs is None:
            return False
        local = now.astimezone(self.timezone)
        slot = datetime.combine(local.date(), time(self.config.purge_hour, self.config.purge_minute), self.timezone)
        return local >= slot and self.purged_on != local.date()

    async def _step(self, name: str, run: Callable[[], Awaitable[object]]) -> None:
        try:
            result = await run()
        except Exception:
            self.logger.exception("screenshot_control_step_failed", step=name)
            return
        if result:
            self.logger.info("screenshot_control_step_done", step=name, result=str(result))
