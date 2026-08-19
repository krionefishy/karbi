import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.domain import Bot
from backend.modules.notifications.infrastructure.postgres import BotModel, NotificationRepository
from backend.shared.security import CredentialCipher


class BotNotFoundError(Exception):
    pass


class DuplicateBotError(Exception):
    pass


class BotRegistry:
    """Bots live in the database so a new one needs no deploy and no config edit."""

    def __init__(self, session: AsyncSession, repository: NotificationRepository, cipher: CredentialCipher) -> None:
        self.session = session
        self.repository = repository
        self.cipher = cipher

    async def register(self, *, code: str, username: str, title: str, token: str) -> Bot:
        fingerprint = self.cipher.fingerprint(token)
        if await self.repository.bot_by_code(code) is not None:
            raise DuplicateBotError(f"Bot {code} already exists")
        if await self.repository.fingerprint_exists(fingerprint):
            raise DuplicateBotError("This token is already registered for another bot")
        model = self.repository.add_bot(
            code=code,
            username=username.lstrip("@"),
            title=title,
            encrypted_token=self.cipher.encrypt(token),
            fingerprint=fingerprint,
        )
        await self.session.commit()
        return self.to_domain(model)

    async def active(self) -> list[Bot]:
        return [self.to_domain(model) for model in await self.repository.active_bots()]

    async def by_code(self, code: str) -> Bot:
        model = await self.repository.bot_by_code(code)
        if model is None or not model.is_active:
            raise BotNotFoundError(code)
        return self.to_domain(model)

    async def token(self, bot_id: uuid.UUID) -> str:
        model = await self.repository.bot(bot_id)
        if model is None:
            raise BotNotFoundError(str(bot_id))
        return self.cipher.decrypt(model.encrypted_token)

    @staticmethod
    def to_domain(model: BotModel) -> Bot:
        return Bot(model.id, model.code, model.username, model.title)
