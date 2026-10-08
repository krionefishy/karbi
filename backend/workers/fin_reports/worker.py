import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import structlog

from backend.modules.fin_reports.application import FactsBuilder, StockSnapshots
from backend.modules.fin_reports.infrastructure.postgres import FinReportsRepository
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.shared.heartbeat import touch_heartbeat
from backend.shared.settings import Settings
from backend.storage.pg import Database


class FinReportsWorker:
    """Раз в `build_interval_minutes` складывает новые отчёты реализации подключённых кабинетов.

    Отчёт складывается один раз, поэтому проход без новых отчётов почти
    бесплатен. Подключённый и активный кабинет обходится всегда — ключ на
    шлюзе тут ни при чём: зеркало уже прочитало, что смогло, а сложить
    прочитанное можно и при отозванном ключе.
    """

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.config = settings.fin_reports
        self._now = now or (lambda: datetime.now(UTC))
        self.builder = FactsBuilder(database, heartbeat=touch_heartbeat)
        self.snapshots = StockSnapshots(database, timezone=ZoneInfo(self.config.timezone))
        self.logger = structlog.get_logger("fin_reports_worker")
        self._stop = asyncio.Event()
        self._built_at: datetime | None = None

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self.logger.info("worker_started")
        while not self._stop.is_set():
            touch_heartbeat()
            try:
                await self.tick()
            except Exception:
                self.logger.exception("fin_reports_tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.worker.poll_interval_seconds)
            except TimeoutError:
                continue
        self.logger.info("worker_stopped")

    def due(self, now: datetime) -> bool:
        """Пора ли: после старта — сразу, дальше раз в интервал; недособранное — без паузы."""
        if self._built_at is None:
            return True
        return now - self._built_at >= timedelta(minutes=self.config.build_interval_minutes)

    async def tick(self) -> int:
        now = self._now()
        if not self.due(now):
            return 0
        async with self.database.session() as session:
            tracked = await FinReportsRepository(session).tracked_seller_ids()
            sellers = [seller.id for seller in await SellerRepository(session).list_sellers() if seller.id in tracked]
        built = left = 0
        for seller_id in sellers:
            if self._stop.is_set():
                break
            touch_heartbeat()
            outcome = await self.build(seller_id, now)
            built += outcome[0]
            left += outcome[1]
            await self.snapshot(seller_id, now)
        # Потолок за проход не дал дойти до конца — следующий проход сразу, без интервала.
        self._built_at = None if left else now
        if built:
            self.logger.info("fin_reports_built", reports=built, left=left)
        return built

    async def snapshot(self, seller_id: uuid.UUID, now: datetime) -> None:
        """Остаток на конец закрытой недели — один раз в неделю, в понедельник после трёх."""
        try:
            week_end = await self.snapshots.take(seller_id, now=now)
        except Exception:
            self.logger.exception("fin_reports_snapshot_failed", seller_id=str(seller_id))
            return
        if week_end is not None:
            self.logger.info("fin_reports_stock_snapshot", seller_id=str(seller_id), week_end=week_end.isoformat())

    async def build(self, seller_id: uuid.UUID, now: datetime) -> tuple[int, int]:
        """Один кабинет: (сложено, осталось). Сбой одного кабинета не трогает остальных."""
        try:
            outcome = await self.builder.build(seller_id, limit=self.config.reports_per_run, now=now)
        except Exception:
            self.logger.exception("fin_reports_build_failed", seller_id=str(seller_id))
            return 0, 0
        return outcome.built, outcome.left
