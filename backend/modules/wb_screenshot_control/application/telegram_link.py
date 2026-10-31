"""Ссылка привязки Telegram: `t.me/<бот>?start=<токен>` на 15 минут, одна живая на сотрудника.

Токен — bearer: кто откроет ссылку, тот и привяжет свой Telegram. Поэтому он случайный,
одноразовый, короткоживущий, а в базе лежит только sha256.
"""

import hashlib
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotNotFoundError, BotRegistry
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.wb_screenshot_control.application.errors import ServiceError
from backend.modules.wb_screenshot_control.infrastructure.postgres.telegram import TelegramStore
from backend.shared.settings.config import ScreenshotControlConfig

Clock = Callable[[], datetime]
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

    async def issue(self, user_id: uuid.UUID) -> dict[str, Any]:
        """Новая ссылка гасит прежние неиспользованные. Без флага или без интерактивного бота — `telegram_disabled`."""
        if not self.config.telegram_enabled:
            raise ServiceError("telegram_disabled", "Приём скриншотов через Telegram выключен")
        try:
            bot = await BotRegistry(self.session, NotificationRepository(self.session)).by_code(
                self.config.notification_bot
            )
        except BotNotFoundError as error:
            raise ServiceError("telegram_disabled", BOT_MISSING) from error
        if not bot.interactive or "{token}" not in bot.invite_link_template:
            raise ServiceError("telegram_disabled", BOT_MISSING)
        now = self.now()
        expires_at = now + timedelta(minutes=self.config.link_token_ttl_minutes)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        await self.store.drop_tokens(user_id, now)
        await self.store.add_token(user_id, hash_link_token(token), expires_at)
        await self.session.commit()
        return {"url": bot.invite_link_template.replace("{token}", token), "expires_at": expires_at}
