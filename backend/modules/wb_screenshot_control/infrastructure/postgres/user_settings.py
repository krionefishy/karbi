"""Настройки менеджера в модуле (`user_settings`). Транзакцией управляет вызывающий."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_screenshot_control.infrastructure.postgres.models import UserSettingsModel


class UserSettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def timezone(self, user_id: uuid.UUID) -> str | None:
        """Пояс, выбранный менеджером; `None` — не выбирал, действует пояс по умолчанию."""
        return await self.session.scalar(select(UserSettingsModel.timezone).where(UserSettingsModel.user_id == user_id))

    async def set_timezone(self, user_id: uuid.UUID, timezone: str, now: datetime) -> None:
        statement = insert(UserSettingsModel).values(user_id=user_id, timezone=timezone, updated_at=now)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[UserSettingsModel.user_id], set_={"timezone": timezone, "updated_at": now}
            )
        )
