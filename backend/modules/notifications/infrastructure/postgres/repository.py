import random
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.infrastructure.postgres.models import (
    BotCursorModel,
    BotModel,
    InviteLinkModel,
    OutgoingMessageModel,
    SubscriptionModel,
)


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def active_bots(self) -> list[BotModel]:
        return list(
            await self.session.scalars(select(BotModel).where(BotModel.is_active).order_by(BotModel.created_at))
        )

    async def bot_by_code(self, code: str) -> BotModel | None:
        return await self.session.scalar(select(BotModel).where(BotModel.code == code))

    async def bot(self, bot_id: uuid.UUID) -> BotModel | None:
        return await self.session.get(BotModel, bot_id)

    async def fingerprint_exists(self, fingerprint: str) -> bool:
        return (
            await self.session.scalar(select(BotModel.id).where(BotModel.token_fingerprint == fingerprint)) is not None
        )

    def add_bot(self, *, code: str, username: str, title: str, encrypted_token: str, fingerprint: str) -> BotModel:
        bot = BotModel(
            code=code,
            username=username,
            title=title,
            encrypted_token=encrypted_token,
            token_fingerprint=fingerprint,
        )
        self.session.add(bot)
        return bot

    async def cursor(self, bot_id: uuid.UUID) -> int:
        row = await self.session.get(BotCursorModel, bot_id)
        return row.last_update_id if row else 0

    async def save_cursor(self, bot_id: uuid.UUID, last_update_id: int) -> None:
        statement = insert(BotCursorModel).values(bot_id=bot_id, last_update_id=last_update_id)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["bot_id"],
                set_={"last_update_id": statement.excluded.last_update_id, "updated_at": datetime.now(UTC)},
                # Updates arrive in order; an out-of-order write would replay /start.
                where=BotCursorModel.last_update_id < statement.excluded.last_update_id,
            )
        )

    def add_invite(
        self,
        *,
        token: str,
        bot_id: uuid.UUID,
        seller_id: uuid.UUID,
        seller_name: str,
        created_by: uuid.UUID | None,
        ttl_hours: int,
    ) -> InviteLinkModel:
        invite = InviteLinkModel(
            token=token,
            bot_id=bot_id,
            seller_id=seller_id,
            seller_name=seller_name,
            created_by=created_by,
            expires_at=datetime.now(UTC) + timedelta(hours=ttl_hours),
        )
        self.session.add(invite)
        return invite

    async def claim_invite(self, bot_id: uuid.UUID, token: str) -> InviteLinkModel | None:
        """Spend the invitation. A used, revoked or expired token resolves to nothing."""
        now = datetime.now(UTC)
        invite = await self.session.scalar(
            select(InviteLinkModel)
            .where(
                InviteLinkModel.bot_id == bot_id,
                InviteLinkModel.token == token,
                InviteLinkModel.used_at.is_(None),
                InviteLinkModel.revoked_at.is_(None),
                InviteLinkModel.expires_at > now,
            )
            .with_for_update()
        )
        if invite is None:
            return None
        invite.used_at = now
        return invite

    async def revoke_invites(self, bot_id: uuid.UUID, seller_id: uuid.UUID) -> None:
        await self.session.execute(
            update(InviteLinkModel)
            .where(
                InviteLinkModel.bot_id == bot_id,
                InviteLinkModel.seller_id == seller_id,
                InviteLinkModel.used_at.is_(None),
                InviteLinkModel.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(UTC))
        )

    async def subscribe(
        self,
        *,
        bot_id: uuid.UUID,
        chat_id: int,
        seller_id: uuid.UUID,
        seller_name: str,
        telegram_user_id: int | None,
        username: str,
        first_name: str,
    ) -> None:
        statement = insert(SubscriptionModel).values(
            bot_id=bot_id,
            chat_id=chat_id,
            seller_id=seller_id,
            seller_name=seller_name,
            telegram_user_id=telegram_user_id,
            username=username,
            first_name=first_name,
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                constraint="uq_notifications_subscription",
                set_={
                    "seller_name": statement.excluded.seller_name,
                    "telegram_user_id": statement.excluded.telegram_user_id,
                    "username": statement.excluded.username,
                    "first_name": statement.excluded.first_name,
                    "is_active": True,
                    "unlinked_at": None,
                },
            )
        )

    async def unsubscribe_chat(self, bot_id: uuid.UUID, chat_id: int) -> int:
        result = await self.session.execute(
            update(SubscriptionModel)
            .where(
                SubscriptionModel.bot_id == bot_id,
                SubscriptionModel.chat_id == chat_id,
                SubscriptionModel.is_active,
            )
            .values(is_active=False, unlinked_at=datetime.now(UTC))
            .returning(SubscriptionModel.id)
        )
        return len(result.all())

    async def subscriptions_of_chat(self, bot_id: uuid.UUID, chat_id: int) -> list[SubscriptionModel]:
        return list(
            await self.session.scalars(
                select(SubscriptionModel).where(
                    SubscriptionModel.bot_id == bot_id,
                    SubscriptionModel.chat_id == chat_id,
                    SubscriptionModel.is_active,
                )
            )
        )

    async def subscribers(self, bot_id: uuid.UUID, seller_id: uuid.UUID) -> list[int]:
        rows = await self.session.scalars(
            select(SubscriptionModel.chat_id).where(
                SubscriptionModel.bot_id == bot_id,
                SubscriptionModel.seller_id == seller_id,
                SubscriptionModel.is_active,
            )
        )
        return sorted(set(rows))

    async def queue_message(
        self, *, bot_id: uuid.UUID, chat_id: int, dedupe_key: str, template: str, params: dict, text: str
    ) -> bool:
        """Returns False when this exact message was already queued before."""
        statement = insert(OutgoingMessageModel).values(
            bot_id=bot_id,
            chat_id=chat_id,
            dedupe_key=dedupe_key,
            template=template,
            params=params,
            text=text,
        )
        result = await self.session.execute(
            statement.on_conflict_do_nothing(index_elements=["dedupe_key"]).returning(OutgoingMessageModel.id)
        )
        return result.scalar_one_or_none() is not None

    async def due_messages(self, limit: int = 50, bot_id: uuid.UUID | None = None) -> list[OutgoingMessageModel]:
        """Messages ready to go, oldest first, optionally for one bot only.

        Delivery runs a task per bot, so each one asks for its own rows; SKIP
        LOCKED keeps those tasks from ever waiting on each other.
        """
        conditions = [
            OutgoingMessageModel.status == "queued",
            # Both sides on the database clock: next_attempt_at is written
            # by it too, and even milliseconds of skew would hide a row
            # that is due right now.
            OutgoingMessageModel.next_attempt_at <= func.now(),
        ]
        if bot_id is not None:
            conditions.append(OutgoingMessageModel.bot_id == bot_id)
        return list(
            await self.session.scalars(
                select(OutgoingMessageModel)
                .where(*conditions)
                .order_by(OutgoingMessageModel.created_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        )

    async def mark_sent(self, message_id: uuid.UUID, telegram_message_id: int | None) -> None:
        await self.session.execute(
            update(OutgoingMessageModel)
            .where(OutgoingMessageModel.id == message_id)
            .values(
                status="sent",
                sent_at=datetime.now(UTC),
                telegram_message_id=telegram_message_id,
                attempts=OutgoingMessageModel.attempts + 1,
                error=None,
            )
        )

    async def mark_retry(self, message_id: uuid.UUID, error: str, *, max_attempts: int, backoff_seconds: int) -> bool:
        """Give the message another try, or give up once the budget is spent."""
        message = await self.session.get(OutgoingMessageModel, message_id)
        if message is None:
            return False
        message.attempts += 1
        message.error = error[:1000]
        if message.attempts >= max_attempts:
            message.status = "failed"
            return False
        message.next_attempt_at = datetime.now(UTC) + timedelta(
            seconds=backoff_seconds * (2 ** max(message.attempts - 1, 0))
        )
        return True

    async def mark_rate_limited(self, message_id: uuid.UUID, error: str, *, retry_after_seconds: float) -> None:
        """Reschedule for when Telegram said to come back, without spending an attempt.

        A rate limit is our pace, not the message's fault: counting it against
        max_attempts would fail perfectly good alerts within minutes.
        """
        delay = retry_after_seconds + random.uniform(0.1, 1.5)
        await self.session.execute(
            update(OutgoingMessageModel)
            .where(OutgoingMessageModel.id == message_id)
            .values(error=error[:1000], next_attempt_at=datetime.now(UTC) + timedelta(seconds=delay))
        )

    async def mark_failed(self, message_id: uuid.UUID, error: str) -> None:
        await self.session.execute(
            update(OutgoingMessageModel)
            .where(OutgoingMessageModel.id == message_id)
            .values(status="failed", error=error[:1000], attempts=OutgoingMessageModel.attempts + 1)
        )
