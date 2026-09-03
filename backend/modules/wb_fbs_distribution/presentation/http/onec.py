import hmac
from datetime import datetime

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from backend.modules.wb_fbs_distribution.application import ONEC, SnapshotRejected, SnapshotService
from backend.modules.wb_fbs_distribution.domain import StockLine, StockSnapshot
from backend.modules.wb_fbs_distribution.infrastructure.postgres import FbsDistributionRepository
from backend.modules.wb_fbs_distribution.presentation.http.schemas import SnapshotStateResponse
from backend.shared.settings import Settings

router = APIRouter(prefix="/onec", tags=["onec"])

EXCHANGE_OFF = "Обмен с 1С не настроен: на сервере не задан ONEC_TOKEN"
BAD_TOKEN = "Неверный токен обмена"


class OnecLine(BaseModel):
    """Строка снимка в том виде, в котором её шлёт 1С."""

    item_id: str = Field(default="", max_length=128)
    barcode: str = Field(default="", max_length=64)
    name: str = Field(default="", max_length=512)
    characteristic: str = Field(default="", max_length=255)
    quantity: int


class OnecSnapshot(BaseModel):
    """Абсолютный снимок остатков целиком — не изменения."""

    generated_at: datetime
    lines: list[OnecLine] = Field(max_length=100_000)


def _authorize(settings: Settings, authorization: str | None) -> None:
    """Свой токен, а не сотрудничий JWT: 1С — машина, у неё нет логина.

    Пустой токен в настройках значит «обмен выключен»: честный 503 вместо
    эндпоинта, открытого всем.
    """
    expected = settings.fbs_distribution.onec_token
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, EXCHANGE_OFF)
    supplied = ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_TOKEN, headers={"WWW-Authenticate": "Bearer"})


def snapshot_state_response(state, reserve_units: int) -> SnapshotStateResponse:
    return SnapshotStateResponse(
        snapshot_id=state.snapshot_id,
        source=state.source,
        generated_at=state.generated_at.isoformat() if state.generated_at else None,
        received_at=state.received_at.isoformat() if state.received_at else None,
        lines=state.lines,
        stale=state.stale,
        pools=state.pools,
        on_hand_total=state.on_hand_total,
        available_total=state.available_total,
        reserve_units=reserve_units,
    )


@router.get("/ping")
@inject
async def ping(
    settings: FromDishka[Settings],
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    """Проверка связи и токена до того, как писать выгрузку на стороне 1С."""
    _authorize(settings, authorization)
    return {"status": "ok"}


@router.post("/stocks", response_model=SnapshotStateResponse)
@inject
async def take_stocks(
    payload: OnecSnapshot,
    settings: FromDishka[Settings],
    snapshots: FromDishka[SnapshotService],
    repository: FromDishka[FbsDistributionRepository],
    authorization: str | None = Header(default=None),
) -> SnapshotStateResponse:
    """Принять абсолютный снимок остатков от 1С.

    Снимок берётся целиком или отвергается целиком с причиной в ответе; отказ
    тоже попадает в журнал обменов. Повторная отправка того же снимка безопасна
    и ничего не задваивает.
    """
    _authorize(settings, authorization)
    lines = []
    for index, line in enumerate(payload.lines, start=1):
        item_id = line.item_id.strip() or line.barcode.strip()
        if not item_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Строка {index}: нет ни идентификатора номенклатуры, ни баркода",
            )
        lines.append(
            StockLine(
                item_id=item_id,
                barcode=line.barcode.strip(),
                name=line.name.strip(),
                characteristic=line.characteristic.strip(),
                quantity=line.quantity,
            )
        )
    snapshot = StockSnapshot(generated_at=payload.generated_at, lines=tuple(lines))
    try:
        state = await snapshots.accept(snapshot, source=ONEC)
    except SnapshotRejected as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    reserve = (await repository.settings()).reserve_units
    return snapshot_state_response(state, reserve_units=reserve)
