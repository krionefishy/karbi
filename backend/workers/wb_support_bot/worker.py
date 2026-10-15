import asyncio
import random
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import structlog

from backend.modules.wb_core.application import ChatMirror
from backend.modules.wb_core.domain import EGRESS_SERVABLE
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_support_bot.application import BotPolicy, ChatSender, SupportBotProcessor
from backend.modules.wb_support_bot.domain import CampaignRules, Pace, SendWindow
from backend.modules.wb_support_bot.infrastructure.postgres import SupportBotRepository
from backend.shared.heartbeat import touch_heartbeat
from backend.shared.settings import Settings
from backend.storage.pg import Database


def policy_from(settings: Settings) -> BotPolicy:
    config = settings.support_bot
    return BotPolicy(
        window=SendWindow(
            start=time(config.window_start_hour),
            end=time.max if config.window_end_hour >= 24 else time(config.window_end_hour),
            timezone=ZoneInfo(config.timezone),
        ),
        pace=Pace(
            interval=timedelta(minutes=config.interval_minutes),
            jitter_min=timedelta(minutes=config.jitter_min_minutes),
            jitter_max=timedelta(minutes=config.jitter_max_minutes),
        ),
        rules=CampaignRules(
            silence=timedelta(hours=config.silence_hours),
            duration=timedelta(days=config.campaign_days),
            max_campaigns=config.max_campaigns,
            max_idle=timedelta(days=config.max_idle_days),
        ),
        daily_cap=config.daily_cap_per_seller,
        min_gap=timedelta(seconds=config.min_gap_seconds),
        batch=config.batch_per_tick,
        history=timedelta(days=config.history_days),
        mirror_stale=timedelta(minutes=config.mirror_stale_minutes),
        canary_percent=config.canary_percent,
    )


class SupportBotWorker:
    """Раз в `tick_minutes` проходит по подключённым кабинетам.

    Один проход по кабинету — разбор диалогов из зеркала и отправка того, что
    пора. Ошибка одного кабинета не останавливает остальные; отправку по
    кабинету с ошибкой доступа бот снимает сам и пишет причину в карточку.
    """

    def __init__(
        self,
        database: Database,
        sender: ChatSender,
        settings: Settings,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.database = database
        self.sender = sender
        self.settings = settings
        self.policy = policy_from(settings)
        self._now = now or (lambda: datetime.now(UTC))
        self.logger = structlog.get_logger("wb_support_bot_worker")
        self._stop = asyncio.Event()
        self._last_tick: datetime | None = None

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self.logger.info("worker_started")
        while not self._stop.is_set():
            touch_heartbeat()
            try:
                await self.tick()
            except Exception:
                self.logger.exception("support_bot_tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.settings.worker.poll_interval_seconds)
            except TimeoutError:
                continue
        self.logger.info("worker_stopped")

    def _due(self, now: datetime) -> bool:
        gap = timedelta(minutes=self.settings.support_bot.tick_minutes)
        return self._last_tick is None or now - self._last_tick >= gap

    async def tick(self) -> int:
        now = self._now()
        if not self._due(now):
            return 0
        self._last_tick = now
        async with self.database.session() as session:
            tracked = await SupportBotRepository(session).tracked_seller_ids()
            # Архивный кабинет остаётся подключённым, но ключа у шлюза уже нет.
            servable = {
                seller.id
                for seller in await SellerRepository(session).list_sellers()
                if seller.egress_status in EGRESS_SERVABLE
            }
        handled = 0
        for seller_id in sorted(tracked & servable, key=str):
            if self._stop.is_set():
                break
            touch_heartbeat()
            try:
                async with self.database.session() as session:
                    processor = SupportBotProcessor(
                        SupportBotRepository(session),
                        ChatMirror(session),
                        self.sender,
                        self.policy,
                        rng=random.Random(),
                    )
                    report = await processor.process_seller(seller_id, now=now)
            except Exception:
                self.logger.exception("support_bot_seller_failed", seller_id=str(seller_id))
                continue
            handled += 1
            self.logger.info(
                "support_bot_seller_processed",
                seller_id=str(seller_id),
                dialogs=report.dialogs,
                campaigns_started=report.campaigns_started,
                sent=report.sent,
                dry_run=report.dry_run,
                failed=report.failed,
                skipped=report.skipped,
            )
        return handled
