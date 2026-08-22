"""Bots in the admin section.

Registering a bot is the one place a messenger token crosses this server. It is
passed straight to the relay and never written down here — see BotAdminService.
"""

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Response, status

from backend.app.http.authentication import CurrentAdmin
from backend.modules.notifications.application import BotAdminService, BotNotFoundError, BotRejectedError
from backend.modules.notifications.domain import Bot, MessengerTemporaryError
from backend.modules.notifications.presentation.http.schemas import BotCreate, BotResponse

router = APIRouter(prefix="/admin/bots", tags=["admin-bots"])


def bot_response(bot: Bot) -> BotResponse:
    return BotResponse(
        id=str(bot.id),
        code=bot.code,
        title=bot.title,
        invite_link_template=bot.invite_link_template,
    )


@router.get("", response_model=list[BotResponse])
@inject
async def list_bots(_: CurrentAdmin, service: FromDishka[BotAdminService]) -> list[BotResponse]:
    return [bot_response(bot) for bot in await service.list_bots()]


@router.post("", response_model=BotResponse, status_code=status.HTTP_201_CREATED)
@inject
async def register_bot(
    payload: BotCreate,
    _: CurrentAdmin,
    service: FromDishka[BotAdminService],
) -> BotResponse:
    try:
        bot = await service.register(
            code=payload.code,
            title=payload.title.strip(),
            token=payload.token.get_secret_value().strip(),
        )
    except BotRejectedError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Мессенджер отклонил токен: {error}") from error
    except MessengerTemporaryError as error:
        # The relay is the only way to check a token, so without it we cannot
        # honestly say whether the bot is good.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Сервис доставки недоступен, попробуйте позже"
        ) from error
    return bot_response(bot)


@router.delete("/{code}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_bot(code: str, _: CurrentAdmin, service: FromDishka[BotAdminService]) -> Response:
    try:
        await service.delete(code)
    except BotNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Бот не найден") from error
    except MessengerTemporaryError as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Сервис доставки недоступен, попробуйте позже"
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
