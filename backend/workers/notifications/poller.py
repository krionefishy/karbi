import asyncio
import logging

from backend.modules.notifications.application import SubscriptionService
from backend.modules.notifications.domain import Bot
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.telegram import (
    TelegramClient,
    TelegramConflictError,
    TelegramPermanentError,
    TelegramTemporaryError,
)
from backend.storage.pg import Database

RETRY_SECONDS = 5.0


class BotPoller:
    """Long polling for one bot.

    One token, one poller — Telegram answers 409 to a second getUpdates on the
    same token and both stop receiving anything. Different bots poll in
    parallel without any coordination, since the limit is per token.
    """

    def __init__(
        self,
        database: Database,
        client: TelegramClient,
        bot: Bot,
        token: str,
        invite_ttl_hours: int,
    ) -> None:
        self.database = database
        self.client = client
        self.bot = bot
        self.token = token
        self.invite_ttl_hours = invite_ttl_hours
        self.logger = logging.getLogger("notifications.poller")

    async def run(self) -> None:
        offset = await self._start_offset()
        self.logger.info("bot_poller_started", extra={"bot": self.bot.code, "offset": offset})
        while True:
            try:
                updates = await self.client.updates(self.token, offset)
            except TelegramConflictError:
                # Someone else holds this token: back off instead of fighting.
                self.logger.warning("bot_poller_conflict", extra={"bot": self.bot.code})
                await asyncio.sleep(RETRY_SECONDS)
                continue
            except TelegramTemporaryError as error:
                self.logger.warning("bot_poller_retry", extra={"bot": self.bot.code, "error": str(error)})
                await asyncio.sleep(RETRY_SECONDS)
                continue
            except TelegramPermanentError as error:
                self.logger.error("bot_poller_rejected", extra={"bot": self.bot.code, "error": str(error)})
                await asyncio.sleep(RETRY_SECONDS)
                continue
            for update in updates:
                await self.handle(update)
                offset = update.update_id + 1

    async def _start_offset(self) -> int:
        async with self.database.session() as session:
            last = await NotificationRepository(session).cursor(self.bot.id)
        return last + 1 if last else 0

    async def handle(self, update) -> None:
        async with self.database.session() as session:
            repository = NotificationRepository(session)
            service = SubscriptionService(session, repository, invite_ttl_hours=self.invite_ttl_hours)
            await service.handle_update(self.bot, update)
            await repository.save_cursor(self.bot.id, update.update_id)
            await session.commit()
