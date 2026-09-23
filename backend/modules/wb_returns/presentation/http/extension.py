"""Что зовёт расширение и откуда бот берёт картинку QR.

Расширение — машина без логина: у неё свой токен, выданный за код пары, а не
сотрудничий JWT. Картинка QR отдаётся по подписанной ссылке без входа: бот
кладёт её в сообщение, а менеджер открывает на кассе.
"""

import uuid
from datetime import date

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from backend.modules.wb_returns.application import (
    STATES,
    ExtensionService,
    ExtensionUnauthorizedError,
    PairingCodeInvalidError,
)

router = APIRouter(prefix="/wb/returns", tags=["wb-returns-extension"])

BAD_TOKEN = "Токен расширения не подходит: подключите расширение заново по коду из бота"


class PairRequest(BaseModel):
    code: str = Field(min_length=4, max_length=8)
    install_id: str = Field(min_length=8, max_length=64)
    browser: str = Field(default="", max_length=255)


class PairResponse(BaseModel):
    token: str
    seller_id: str
    seller_name: str


class CodeRow(BaseModel):
    date: str = Field(min_length=10, max_length=32)
    code: str = Field(default="", max_length=16)
    ext_code: str = Field(default="", max_length=16)
    qr: str = Field(default="", max_length=2048)
    ext_qr: str = Field(default="", max_length=2048)


class CodesRequest(BaseModel):
    codes: list[CodeRow] = Field(max_length=31)


class DeliveriesRequest(BaseModel):
    items: list[dict] = Field(default_factory=list, max_length=500)


class HeartbeatRequest(BaseModel):
    state: str = Field(default="ok", max_length=16)
    error: str | None = Field(default=None, max_length=1000)


class HeartbeatResponse(BaseModel):
    seller_name: str
    has_code_today: bool
    refresh_code: bool


async def _install(service: ExtensionService, authorization: str | None):  # noqa: ANN202 — модель ORM
    supplied = ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    try:
        return await service.authenticate(supplied)
    except ExtensionUnauthorizedError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_TOKEN, headers={"WWW-Authenticate": "Bearer"}) from error


@router.post("/extension/pair", response_model=PairResponse)
@inject
async def pair(payload: PairRequest, service: FromDishka[ExtensionService]) -> PairResponse:
    try:
        result = await service.pair(payload.code, install_id=payload.install_id, browser=payload.browser)
    except PairingCodeInvalidError as error:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Код не подошёл: он уже использован или устарел. Запросите новый командой /extension",
        ) from error
    return PairResponse(token=result.token, seller_id=str(result.seller_id), seller_name=result.seller_name)


@router.post("/extension/codes")
@inject
async def codes(
    payload: CodesRequest,
    service: FromDishka[ExtensionService],
    authorization: str | None = Header(default=None),
) -> dict[str, int]:
    install = await _install(service, authorization)
    result = await service.ingest_codes(install, [row.model_dump() for row in payload.codes])
    return {"accepted": result.accepted, "replied": len(result.replied_chats)}


@router.post("/extension/deliveries")
@inject
async def deliveries(
    payload: DeliveriesRequest,
    service: FromDishka[ExtensionService],
    authorization: str | None = Header(default=None),
) -> dict[str, int]:
    install = await _install(service, authorization)
    return {"accepted": await service.ingest_deliveries(install, payload.items)}


@router.post("/extension/heartbeat", response_model=HeartbeatResponse)
@inject
async def heartbeat(
    payload: HeartbeatRequest,
    service: FromDishka[ExtensionService],
    authorization: str | None = Header(default=None),
) -> HeartbeatResponse:
    install = await _install(service, authorization)
    state = payload.state if payload.state in STATES else "error"
    result = await service.heartbeat(install, state=state, error=payload.error)
    return HeartbeatResponse(
        seller_name=result.seller_name, has_code_today=result.has_code_today, refresh_code=result.refresh_code
    )


@router.get("/qr/{seller_id}/{day}/{signature}.png", include_in_schema=False)
@inject
async def qr_image(seller_id: uuid.UUID, day: date, signature: str, service: FromDishka[ExtensionService]) -> Response:
    if not service.verify_qr(seller_id, day, signature):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    png = await service.qr_png(seller_id, day)
    if png is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Кода на этот день нет")
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})
