"""SQL привязки Telegram: одноразовые токены deep-link и живые привязки. Транзакцией управляет вызывающий."""

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_screenshot_control.infrastructure.postgres.models import (
    SubmissionModel,
    TelegramLinkModel,
    TelegramLinkTokenModel,
)


class TelegramStore:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def drop_tokens(self, user_id: uuid.UUID, now: datetime) -> None:
        """Прежние неиспользованные ссылки сотрудника гаснут: живая — только последняя."""
        await self.session.execute(
            update(TelegramLinkTokenModel)
            .where(
                TelegramLinkTokenModel.user_id == user_id,
                TelegramLinkTokenModel.used_at.is_(None),
                TelegramLinkTokenModel.expires_at > now,
            )
            .values(expires_at=now)
        )

    async def add_token(self, user_id: uuid.UUID, token_hash: str, expires_at: datetime) -> TelegramLinkTokenModel:
        token = TelegramLinkTokenModel(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self.session.add(token)
        await self.session.flush()
        return token

    async def claim_token(self, token_hash: str, now: datetime) -> TelegramLinkTokenModel | None:
        """Погасить токен. Использованный, погашенный и просроченный не находится."""
        token = await self.session.scalar(
            select(TelegramLinkTokenModel)
            .where(
                TelegramLinkTokenModel.token_hash == token_hash,
                TelegramLinkTokenModel.used_at.is_(None),
                TelegramLinkTokenModel.expires_at > now,
            )
            .with_for_update()
        )
        if token is not None:
            token.used_at = now
        return token

    async def link_by_telegram(self, telegram_user_id: int) -> TelegramLinkModel | None:
        return await self.session.scalar(
            select(TelegramLinkModel).where(
                TelegramLinkModel.telegram_user_id == telegram_user_id, TelegramLinkModel.revoked_at.is_(None)
            )
        )

    async def link_of_user(self, user_id: uuid.UUID) -> TelegramLinkModel | None:
        return await self.session.scalar(
            select(TelegramLinkModel).where(
                TelegramLinkModel.user_id == user_id, TelegramLinkModel.revoked_at.is_(None)
            )
        )

    async def add_link(
        self, *, user_id: uuid.UUID, telegram_user_id: int, chat_id: int, username: str
    ) -> TelegramLinkModel | None:
        """`None` — параллельно появилась живая привязка того же сотрудника или того же Telegram."""
        link = TelegramLinkModel(
            user_id=user_id, telegram_user_id=telegram_user_id, chat_id=chat_id, username=username[:255]
        )
        try:
            async with self.session.begin_nested():
                self.session.add(link)
        except IntegrityError:
            return None
        return link

    async def revoke(self, link: TelegramLinkModel, now: datetime, *, by: uuid.UUID) -> None:
        link.revoked_at = now
        link.revoked_by = by

    async def uploads_since(self, user_id: uuid.UUID, since: datetime) -> int:
        """Скриншоты сотрудника по всем каналам с момента `since` — для лимита бота."""
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(SubmissionModel)
                .where(SubmissionModel.user_id == user_id, SubmissionModel.received_at > since)
            )
            or 0
        )
