"""Updates coming back from the relay.

Not reachable from the internet: the public vhosts refuse `/api/v1/internal/`
outright, and the only way in is the host listener bound to the relay's address.
The JWT here is the second lock, not the first.

Bodies are never logged on this route: they carry what people write to the bot.
"""

import logging

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Header, HTTPException, status

from backend.modules.notifications.application import BotNotFoundError, BotRegistry, SubscriptionService
from backend.modules.notifications.domain import Update
from backend.modules.notifications.infrastructure.relay import ChannelAuthError, verify_channel_token
from backend.modules.notifications.presentation.http.schemas import UpdateAccepted, UpdatePayload
from backend.shared.settings import Settings

logger = logging.getLogger("notifications.internal")

router = APIRouter(prefix="/internal/telegram", tags=["internal"], include_in_schema=False)


@router.post("/updates", response_model=UpdateAccepted)
@inject
async def accept_update(
    payload: UpdatePayload,
    bots: FromDishka[BotRegistry],
    subscriptions: FromDishka[SubscriptionService],
    settings: FromDishka[Settings],
    authorization: str | None = Header(default=None),
) -> UpdateAccepted:
    try:
        verify_channel_token(authorization, settings.relay)
    except ChannelAuthError as error:
        logger.warning("relay_auth_rejected reason=%s", error)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unauthorized") from error

    try:
        bot = await bots.by_code(payload.bot_code)
    except BotNotFoundError as error:
        # Answering 404 would make the relay retry forever on a bot we removed;
        # accepting it lets the relay move its offset past a dead conversation.
        logger.warning("relay_update_for_unknown_bot bot=%s", payload.bot_code)
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown bot") from error

    if not _is_number(payload.recipient):
        # Subscriptions key chats by a numeric id, so a recipient this side
        # cannot store is a contract mismatch, not a delivery failure.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "recipient is not addressable here")

    sender_id = payload.sender.external_id
    external_id = int(sender_id) if sender_id and _is_number(sender_id) else None
    update = Update(
        update_id=payload.event_id,
        chat_id=int(payload.recipient),
        text=payload.text,
        user_id=external_id,
        username=payload.sender.username,
        first_name=payload.sender.display_name,
    )
    accepted = await subscriptions.accept_update(bot, update)
    logger.info("relay_update bot=%s event=%s accepted=%s", bot.code, payload.event_id, accepted)
    return UpdateAccepted(accepted=accepted)


def _is_number(value: str) -> bool:
    return value.lstrip("-").isdigit()
