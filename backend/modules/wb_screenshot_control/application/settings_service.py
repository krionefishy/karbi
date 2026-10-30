"""Настройки менеджера: часовой пояс, в котором кабинет WB показывает ему время отзыва."""

import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_screenshot_control.application.errors import ServiceError
from backend.modules.wb_screenshot_control.domain.timezones import DEFAULT_TIMEZONE, is_known_zone, zone_options
from backend.modules.wb_screenshot_control.infrastructure.postgres.user_settings import UserSettingsRepository
from backend.shared.settings.config import ScreenshotControlConfig


def default_timezone(config: ScreenshotControlConfig) -> str:
    """Пояс модуля, если он из списка менеджера; иначе Москва."""
    return config.timezone if is_known_zone(config.timezone) else DEFAULT_TIMEZONE


def effective_timezone(stored: str | None, config: ScreenshotControlConfig) -> str:
    """Выбранный менеджером пояс; не выбирал или пояс выпал из списка — по умолчанию."""
    return stored if stored is not None and is_known_zone(stored) else default_timezone(config)


async def user_timezone(session: AsyncSession, user_id: uuid.UUID, config: ScreenshotControlConfig) -> str:
    return effective_timezone(await UserSettingsRepository(session).timezone(user_id), config)


class SettingsService:
    def __init__(
        self,
        session: AsyncSession,
        config: ScreenshotControlConfig,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.session = session
        self.config = config
        self.now = now

    async def get(self, user_id: uuid.UUID) -> dict[str, Any]:
        return self._view(await user_timezone(self.session, user_id, self.config))

    async def set_timezone(self, user_id: uuid.UUID, timezone: str) -> dict[str, Any]:
        """Новый пояс действует для следующих скринов; уже прочитанные не пересчитываются."""
        if not is_known_zone(timezone):
            raise ServiceError("bad_timezone", "Такого часового пояса нет в списке")
        await UserSettingsRepository(self.session).set_timezone(user_id, timezone, self.now())
        await self.session.commit()
        return self._view(timezone)

    def _view(self, timezone: str) -> dict[str, Any]:
        return {
            "timezone": timezone,
            "zones": [asdict(item) for item in zone_options(self.now())],
            "default": default_timezone(self.config),
        }
