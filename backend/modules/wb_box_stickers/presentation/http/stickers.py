import urllib.parse

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile, status

from backend.app.http.principals import CurrentPrincipal
from backend.modules.wb_box_stickers.application import (
    BoxStickerService,
    StickerBuildError,
    StickerInputError,
    StickerPlan,
)
from backend.modules.wb_box_stickers.presentation.http.schemas import BoxResponse, ItemResponse, PlanResponse
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError

router = APIRouter(prefix="/wb/box-stickers", tags=["wb-box-stickers"])

MAX_FILE_BYTES = 20 * 1024 * 1024


async def _read(upload: UploadFile, what: str) -> bytes:
    data = await upload.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"{what} больше 20 МБ")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{what} пустой")
    return data


async def _workbook(service: BoxStickerService, workbook: UploadFile | None, supply_id: int | None) -> bytes:
    """Excel файлом, как раньше, или по номеру поставки — коробовка, заполненная у нас."""
    if workbook is not None and workbook.filename:
        return await _read(workbook, "Excel")
    if supply_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Нужен Excel коробовки или номер поставки")
    try:
        return await service.workbook_for(supply_id)
    except StickerInputError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error


def plan_response(plan: StickerPlan) -> PlanResponse:
    return PlanResponse(
        supply_id=plan.supply_id,
        seller_id=str(plan.seller_id) if plan.seller_id else None,
        seller_name=plan.seller_name,
        ready=plan.ready,
        problems=plan.problems,
        boxes=[
            BoxResponse(
                position=box.position,
                shk=box.shk,
                package_code=box.package_code,
                page=box.page_index + 1,
                quantity=box.quantity,
                items=[
                    ItemResponse(
                        barcode=item.barcode,
                        quantity=item.quantity,
                        vendor_code=item.vendor_code,
                        tech_size=item.tech_size,
                    )
                    for item in box.items
                ],
            )
            for box in plan.boxes
        ],
    )


@router.post("/check", response_model=PlanResponse)
@inject
async def check_stickers(
    _: CurrentPrincipal,
    service: FromDishka[BoxStickerService],
    stickers: UploadFile = File(...),
    workbook: UploadFile | None = File(default=None),
    supply_id: int | None = Form(default=None),
) -> PlanResponse:
    """Сопоставление без сборки: что куда встанет и что мешает."""
    try:
        plan = await service.plan(await _workbook(service, workbook, supply_id), await _read(stickers, "PDF"))
    except StickerInputError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    except WBPermanentError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except WBTemporaryError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    return plan_response(plan)


@router.post("/build")
@inject
async def build_stickers(
    _: CurrentPrincipal,
    service: FromDishka[BoxStickerService],
    stickers: UploadFile = File(...),
    workbook: UploadFile | None = File(default=None),
    supply_id: int | None = Form(default=None),
) -> Response:
    """PDF в порядке строк Excel; при любом расхождении — 422 со списком проблем."""
    try:
        plan, data = await service.build(await _workbook(service, workbook, supply_id), await _read(stickers, "PDF"))
    except StickerInputError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    except StickerBuildError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "\n".join(error.problems)) from error
    except WBPermanentError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except WBTemporaryError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    filename = f"Стикеры коробов {plan.supply_id}.pdf"
    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="box_stickers_{plan.supply_id}.pdf"; '
                f"filename*=UTF-8''{urllib.parse.quote(filename)}"
            )
        },
    )
