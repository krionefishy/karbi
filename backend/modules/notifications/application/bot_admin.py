"""Registering bots from the interface.

The token passes through this server and is never written down here: it goes
straight to the relay, which stores it encrypted and answers with the bot's name
and the template invitation links are built from.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application.bots import BotNotFoundError, BotRegistry
from backend.modules.notifications.domain import Bot, MessengerPermanentError
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.notifications.infrastructure.relay import RelayClient


class BotRejectedError(Exception):
    """The relay refused the token; the bot is not registered anywhere."""


class BotAdminService:
    def __init__(self, session: AsyncSession, repository: NotificationRepository, relay: RelayClient) -> None:
        self.session = session
        self.repository = repository
        self.relay = relay
        self.registry = BotRegistry(session, repository)
        self.logger = logging.getLogger("notifications.bot_admin")

    async def list_bots(self) -> list[Bot]:
        return await self.registry.active()

    async def register(self, *, code: str, title: str, token: str) -> Bot:
        """Hand the token to the relay first, record the bot only if it took it.

        The order matters: a bot recorded here but unknown to the relay would
        accept events and never deliver them.
        """
        try:
            identity = await self.relay.register_bot(bot_code=code, token=token)
        except MessengerPermanentError as error:
            raise BotRejectedError(str(error)) from error
        if not identity.invite_link_template:
            raise BotRejectedError("the relay returned no invite link template")

        bot = await self.registry.register(
            code=code,
            title=title or identity.title,
            invite_link_template=identity.invite_link_template,
        )
        self.logger.info("bot_registered", extra={"bot": code})
        return bot

    async def delete(self, code: str) -> None:
        """Remove the bot here and on the relay, so no token outlives its bot."""
        try:
            bot = await self.registry.by_code(code)
        except BotNotFoundError:
            raise
        await self.relay.delete_bot(code)
        await self.repository.delete_bot(bot.id)
        await self.session.commit()
        self.logger.info("bot_deleted", extra={"bot": code})
