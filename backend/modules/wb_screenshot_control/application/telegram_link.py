"""Ссылка привязки Telegram: `t.me/<бот>?start=<токен>` на 15 минут, одна живая на сотрудника.

Токен — bearer: кто откроет ссылку, тот и привяжет свой Telegram. Поэтому он случайный,
одноразовый, короткоживущий, а в базе лежит только sha256.
"""

import hashlib
import re
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotNotFoundError, BotRegistry
from backend.modules.notifications.domain import Bot
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.wb_screenshot_control.application.errors import ServiceError
from backend.modules.wb_screenshot_control.infrastructure.postgres.telegram import TelegramStore
from backend.shared.settings.config import ScreenshotControlConfig

Clock = Callable[[], datetime]
_BOT_NAME = re.compile(r"^https://t\.me/([A-Za-z0-9_]{3,64})(?:[/?]|$)")
TOKEN_BYTES = 24
BOT_MISSING = "Telegram-бот ещё не подключён администратором"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def hash_link_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class TelegramLinkService:
    def __init__(self, session: AsyncSession, config: ScreenshotControlConfig, *, now: Clock = _utcnow) -> None:
        self.session = session
        self.config = config
        self.store = TelegramStore(session)
        self.now = now

    async def _bot(self) -> Bot | None:
        """Интерактивный бот модуля с шаблоном ссылки привязки; `None`, пока админ его не зарегистрировал."""
        try:
            bot = await BotRegistry(self.session, NotificationRepository(self.session)).by_code(
                self.config.notification_bot
            )
        except BotNotFoundError:
            return None
        return bot if bot.interactive and "{token}" in bot.invite_link_template else None

    async def availability(self) -> tuple[bool, str | None]:
        """Подключён ли бот и его имя из шаблона ссылки (`https://t.me/<имя>?start={token}`)."""
        bot = await self._bot()
        if bot is None:
            return False, None
        match = _BOT_NAME.match(bot.invite_link_template)
        return True, match.group(1) if match else None

    async def issue(self, user_id: uuid.UUID) -> dict[str, Any]:
        """Новая ссылка гасит прежние неиспользованные. Без интерактивного бота — `telegram_unavailable`."""
        bot = await self._bot()
        if bot is None:
            raise ServiceError("telegram_unavailable", BOT_MISSING)
        now = self.now()
        expires_at = now + timedelta(minutes=self.config.link_token_ttl_minutes)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        await self.store.drop_tokens(user_id, now)
        await self.store.add_token(user_id, hash_link_token(token), expires_at)
        await self.session.commit()
        return {"url": bot.invite_link_template.replace("{token}", token), "expires_at": expires_at}
