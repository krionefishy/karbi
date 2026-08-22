"""Fetching inbound messages from the relay.

The relay cannot reach this server — its hosting refuses foreign addresses on
every port — so the direction is inverted: one loop per bot asks the relay for
everything past our cursor and holds the request while the channel is quiet.

The cursor in Postgres is the only bookkeeping. There is no acknowledgement to
lose: a crash between "handled" and "answered" costs a repeated fetch, and the
repeat is dropped by the same cursor.
"""

import asyncio
import logging

from backend.modules.notifications.application import SubscriptionService
from backend.modules.notifications.domain import Bot, Update
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.relay import RelayClient, RelayUpdate
from backend.modules.notifications.infrastructure.telegram import TelegramTemporaryError
from backend.storage.pg import Database

RETRY_SECONDS = 5.0


class UpdateFetcher:
    """One bot's inbound loop; the supervisor runs one of these per bot."""

    def __init__(
        self,
        database: Database,
        client: RelayClient,
        bot: Bot,
        *,
        invite_ttl_hours: int,
        wait_seconds: int,
    ) -> None:
        self.database = database
        self.client = client
        self.bot = bot
        self.invite_ttl_hours = invite_ttl_hours
        self.wait_seconds = wait_seconds
        self.logger = logging.getLogger("notifications.updates")

    async def run_forever(self) -> None:
        self.logger.info("update_fetcher_started", extra={"bot": self.bot.code})
        while True:
            try:
                await self.fetch_once()
            except asyncio.CancelledError:
                raise
            except TelegramTemporaryError as error:
                # The relay being down is normal weather: it keeps the updates.
                self.logger.warning("update_fetch_retry", extra={"bot": self.bot.code, "error": str(error)})
                await asyncio.sleep(RETRY_SECONDS)
            except Exception:
                self.logger.exception("update_fetch_failed", extra={"bot": self.bot.code})
                await asyncio.sleep(RETRY_SECONDS)

    async def fetch_once(self) -> int:
        async with self.database.session() as session:
            after = await NotificationRepository(session).cursor(self.bot.id)

        updates = await self.client.updates(bot_code=self.bot.code, after=after, wait=self.wait_seconds)

        handled = 0
        for update in updates:
            if not await self.handle(update):
                continue
            handled += 1
        if handled:
            self.logger.info("updates_handled", extra={"bot": self.bot.code, "count": handled})
        return handled

    async def handle(self, update: RelayUpdate) -> bool:
        if not _is_number(update.recipient):
            # Subscriptions key chats by a numeric id. Skipping without moving
            # the cursor would ask the relay for this same update forever, so
            # the cursor steps over it and the loop keeps going.
            self.logger.error(
                "update_recipient_not_addressable",
                extra={"bot": self.bot.code, "event": update.event_id},
            )
            async with self.database.session() as session:
                await NotificationRepository(session).save_cursor(self.bot.id, update.event_id)
                await session.commit()
            return False
        async with self.database.session() as session:
            repository = NotificationRepository(session)
            service = SubscriptionService(session, repository, invite_ttl_hours=self.invite_ttl_hours)
            return await service.accept_update(self.bot, _to_domain(update))


def _to_domain(update: RelayUpdate) -> Update:
    return Update(
        update_id=update.event_id,
        chat_id=int(update.recipient),
        text=update.text,
        user_id=int(update.external_id) if update.external_id and _is_number(update.external_id) else None,
        username=update.username,
        first_name=update.display_name,
    )


def _is_number(value: str) -> bool:
    return value.lstrip("-").isdigit()
