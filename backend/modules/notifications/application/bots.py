import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.domain import Bot
from backend.modules.notifications.infrastructure.postgres import BotModel, NotificationRepository


class BotNotFoundError(Exception):
    pass


class DuplicateBotError(Exception):
    pass


class BotRegistry:
    """Bots live in the database so a new one needs no deploy and no config edit.

    No cipher here any more: bot tokens live on the relay, and this registry has
    nothing left to decrypt.
    """

    def __init__(self, session: AsyncSession, repository: NotificationRepository) -> None:
        self.session = session
        self.repository = repository

    async def register(self, *, code: str, title: str, invite_link_template: str) -> Bot:
        """Record a bot the relay has already accepted.

        Idempotent on purpose: rotating a token re-registers the same code on the
        relay, and this side must follow rather than refuse.
        """
        model = await self.repository.bot_by_code(code)
        if model is None:
            model = self.repository.add_bot(code=code, title=title, invite_link_template=invite_link_template)
        else:
            model.title = title
            model.invite_link_template = invite_link_template
            model.is_active = True
        await self.session.commit()
        return self.to_domain(model)

    async def active(self) -> list[Bot]:
        return [self.to_domain(model) for model in await self.repository.active_bots()]

    async def by_code(self, code: str) -> Bot:
        model = await self.repository.bot_by_code(code)
        if model is None or not model.is_active:
            raise BotNotFoundError(code)
        return self.to_domain(model)

    async def by_id(self, bot_id: uuid.UUID) -> Bot:
        model = await self.repository.bot(bot_id)
        if model is None:
            raise BotNotFoundError(str(bot_id))
        return self.to_domain(model)

    @staticmethod
    def to_domain(model: BotModel) -> Bot:
        return Bot(model.id, model.code, model.title, model.invite_link_template)
