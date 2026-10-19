import urllib.parse

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_box_stickers.application import (
    PackingPlan,
    PackingService,
    StickerBuildError,
    StickerInputError,
)
from backend.modules.wb_box_stickers.presentation.http.schemas import (
    PackedBoxResponse,
    PackingPlanResponse,
    RecentPackingResponse,
)
from backend.modules.wb_box_stickers.presentation.http.stickers import _read
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError

router = APIRouter(prefix="/wb/box-stickers/packing", tags=["wb-box-stickers"])

MAX_TEXT_CHARS = 200_000


def packing_response(plan: PackingPlan) -> PackingPlanResponse:
    return PackingPlanResponse(
        supply_id=plan.supply_id,
        seller_id=plan.seller_id,
        seller_name=plan.seller_name,
        ready=plan.ready,
        problems=plan.problems,
        boxes=[
            PackedBoxResponse(
                position=box.position,
                shk=box.shk,
                package_code=box.package_code,
                barcode=box.barcode,
                quantity=box.quantity,
            )
            for box in plan.boxes
        ],
    )


def _text(assembly: str) -> str:
    if len(assembly) > MAX_TEXT_CHARS:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Вставлено слишком много строк")
    if not assembly.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Вставьте строки поставки из таблицы сборки")
    return assembly


@router.post("/check", response_model=PackingPlanResponse)
@inject
async def check_packing(
    _: CurrentPrincipal,
    service: FromDishka[PackingService],
    template: UploadFile = File(...),
    assembly: str = Form(...),
) -> PackingPlanResponse:
    """Раскладка без файла: какой короб что получит и что мешает."""
    try:
        plan = await service.preview(await _read(template, "Шаблон"), _text(assembly))
    except StickerInputError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    except WBPermanentError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except WBTemporaryError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    return packing_response(plan)


@router.post("/build")
@inject
async def build_packing(
    _: CurrentPrincipal,
    service: FromDishka[PackingService],
    template: UploadFile = File(...),
    assembly: str = Form(...),
) -> Response:
    """Заполненный шаблон; он же запоминается под номером поставки для шага стикеров."""
    try:
        plan, data = await service.build(await _read(template, "Шаблон"), _text(assembly))
    except StickerInputError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    except StickerBuildError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "\n".join(error.problems)) from error
    except WBPermanentError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except WBTemporaryError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    filename = f"Коробовка {plan.supply_id}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": (
                f'attachment; filename="packing_{plan.supply_id}.xlsx"; '
                f"filename*=UTF-8''{urllib.parse.quote(filename)}"
            )
        },
    )


@router.get("/recent", response_model=list[RecentPackingResponse])
@inject
async def recent_packings(_: CurrentPrincipal, service: FromDishka[PackingService]) -> list[RecentPackingResponse]:
    """Коробовки, заполненные здесь и ещё не истёкшие: их Excel на шаге стикеров не нужен."""
    return [
        RecentPackingResponse(
            supply_id=item.supply_id, seller_name=item.seller_name, boxes=item.boxes, saved_at=item.saved_at
        )
        for item in await service.recent()
    ]
