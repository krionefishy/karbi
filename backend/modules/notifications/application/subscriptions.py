import secrets
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import templates
from backend.modules.notifications.domain import Bot, Invite, Update
from backend.modules.notifications.infrastructure.postgres import NotificationRepository

# Telegram allows up to 64 characters of [A-Za-z0-9_-] in a start payload.
TOKEN_BYTES = 24


class SubscriptionService:
    """Invitations in, subscriptions out.

    The link carries a random token and nothing else: it travels through
    messengers, and a seller id inside it would leak with every forward.
    """

    def __init__(
        self,
        session: AsyncSession,
        repository: NotificationRepository,
        *,
        invite_ttl_hours: int = 72,
    ) -> None:
        self.session = session
        self.repository = repository
        self.invite_ttl_hours = invite_ttl_hours

    async def create_invite(
        self,
        bot: Bot,
        *,
        seller_id: uuid.UUID,
        seller_name: str,
        created_by: uuid.UUID | None = None,
    ) -> Invite:
        """Issue a fresh single-use link and void the ones nobody opened."""
        try:
            return await self._issue_invite(bot, seller_id, seller_name, created_by)
        except IntegrityError:
            # Two concurrent requests raced past each other's revoke; the partial
            # unique index on live links let only one insert through. Revoke the
            # winner's link too and reissue once.
            await self.session.rollback()
            return await self._issue_invite(bot, seller_id, seller_name, created_by)

    async def _issue_invite(
        self,
        bot: Bot,
        seller_id: uuid.UUID,
        seller_name: str,
        created_by: uuid.UUID | None,
    ) -> Invite:
        await self.repository.revoke_invites(bot.id, seller_id)
        token = secrets.token_urlsafe(TOKEN_BYTES)
        model = self.repository.add_invite(
            token=token,
            bot_id=bot.id,
            seller_id=seller_id,
            seller_name=seller_name,
            created_by=created_by,
            ttl_hours=self.invite_ttl_hours,
        )
        await self.session.commit()
        return Invite(
            token=token,
            # The relay handed this template over at registration; this side only
            # substitutes its own invite token and never learns the link's shape.
            url=bot.invite_link_template.replace("{token}", token),
            expires_at=model.expires_at,
            seller_id=seller_id,
        )

    async def accept_update(self, bot: Bot, update: Update) -> bool:
        """Handle one update at most once.

        The cursor here is the source of truth, not the relay's offset: the relay
        advances only after this call answers, so a lost answer replays the
        update and this check is what stops a second subscription. Returns False
        when the update was already handled.
        """
        last = await self.repository.cursor(bot.id)
        if update.update_id <= last:
            return False
        await self.handle_update(bot, update)
        await self.repository.save_cursor(bot.id, update.update_id)
        await self.session.commit()
        return True

    async def handle_update(self, bot: Bot, update: Update) -> None:
        """Answer one message. Every reply goes through the outgoing queue.

        The caller commits: `accept_update` advances the cursor in the same
        transaction, so a crash cannot subscribe a chat and then re-read the
        same /start with an already spent invitation.
        """
        command, _, argument = update.text.strip().partition(" ")
        # In groups Telegram addresses the bot explicitly: /start@BotName token.
        command = command.partition("@")[0]
        if command == "/start":
            await self._start(bot, update, argument.strip())
        elif command == "/stop":
            await self._stop(bot, update)
        else:
            await self._reply(bot, update, templates.SUBSCRIPTION_NO_TOKEN, {})

    async def _start(self, bot: Bot, update: Update, token: str) -> None:
        if not token:
            await self._reply(bot, update, templates.SUBSCRIPTION_NO_TOKEN, {})
            return
        invite = await self.repository.claim_invite(bot.id, token)
        if invite is None:
            await self._reply(bot, update, templates.SUBSCRIPTION_INVALID_LINK, {})
            return
        await self.repository.subscribe(
            bot_id=bot.id,
            chat_id=update.chat_id,
            seller_id=invite.seller_id,
            seller_name=invite.seller_name,
            telegram_user_id=update.user_id,
            username=update.username,
            first_name=update.first_name,
        )
        await self._reply(
            bot,
            update,
            templates.SUBSCRIPTION_CONFIRMED,
            {"seller_name": invite.seller_name, "bot_title": bot.title or "Marketplace Auto"},
        )

    async def _stop(self, bot: Bot, update: Update) -> None:
        active = await self.repository.subscriptions_of_chat(bot.id, update.chat_id)
        if not active:
            await self._reply(bot, update, templates.SUBSCRIPTION_NOTHING_TO_STOP, {})
            return
        sellers = ", ".join(sorted({item.seller_name for item in active if item.seller_name}))
        await self.repository.unsubscribe_chat(bot.id, update.chat_id)
        await self._reply(bot, update, templates.SUBSCRIPTION_STOPPED, {"sellers": sellers or "—"})

    async def _reply(self, bot: Bot, update: Update, template: str, params: dict) -> None:
        await self.repository.queue_message(
            bot_id=bot.id,
            chat_id=update.chat_id,
            dedupe_key=f"reply:{bot.id}:{update.update_id}",
            template=template,
            params=params,
            text=templates.render(template, params),
        )
