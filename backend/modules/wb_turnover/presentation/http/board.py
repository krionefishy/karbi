import re
import urllib.parse
import uuid

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Response, status

from backend.app.http.principals import CurrentPrincipal
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_turnover.application import (
    BoardInputError,
    BoardService,
    BoardView,
    render_board,
)
from backend.modules.wb_turnover.application.board_report import XLSX_MEDIA_TYPE
from backend.modules.wb_turnover.presentation.http.schemas import (
    BoardAddedResponse,
    BoardAddRequest,
    BoardHiddenRowResponse,
    BoardItemRequest,
    BoardRefreshResponse,
    BoardResponse,
    BoardRowResponse,
    BoardSellerResponse,
)

router = APIRouter(prefix="/board")

SPLIT = re.compile(r"[\s,;]+")


def not_enrolled() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не подключён к автоматизации")


def board_response(view: BoardView) -> BoardResponse:
    return BoardResponse(
        date=view.day.isoformat(),
        window_from=view.window_from.isoformat(),
        window_to=view.window_to.isoformat(),
        window_days=view.window_days,
        red_days=view.red_days,
        yellow_days=view.yellow_days,
        sellers=[
            BoardSellerResponse(
                seller_id=seller.seller_id,
                name=seller.name,
                remains_at=seller.remains_at.isoformat() if seller.remains_at else None,
                remains_stale=seller.remains_stale,
                remains_error=seller.remains_error,
                fbs_at=seller.fbs_at.isoformat() if seller.fbs_at else None,
                orders_without_barcode=seller.orders_without_barcode,
            )
            for seller in view.sellers
        ],
        rows=[
            BoardRowResponse(
                seller_id=row.seller_id,
                seller_name=row.seller_name,
                barcode=row.barcode,
                article=row.article,
                vendor_code=row.vendor_code,
                name=row.name,
                nomenclature=row.nomenclature,
                note=row.note,
                in_catalog=row.in_catalog,
                stock=row.stock,
                orders=row.orders,
                rate=round(row.rate, 1),
                days=row.days,
                status=row.status,
                tone=row.tone,
                fbs=row.fbs,
            )
            for row in view.rows
        ],
        hidden=[
            BoardHiddenRowResponse(
                seller_id=row.seller_id,
                seller_name=row.seller_name,
                barcode=row.barcode,
                note=row.note,
                name=row.name,
                hidden_at=row.hidden_at.isoformat(),
            )
            for row in view.hidden
        ],
    )


@router.get("", response_model=BoardResponse)
@inject
async def board(_: CurrentPrincipal, service: FromDishka[BoardService]) -> BoardResponse:
    return board_response(await service.view())


@router.post("/sellers/{seller_id}/items", response_model=BoardAddedResponse)
@inject
async def add_items(
    seller_id: uuid.UUID, payload: BoardAddRequest, principal: CurrentPrincipal, service: FromDishka[BoardService]
) -> BoardAddedResponse:
    """Вписать баркоды: списком или вставленным текстом — по одному в строке, через запятую или пробел."""
    barcodes = [*payload.barcodes, *SPLIT.split(payload.text)]
    try:
        result = await service.add_items(seller_id, barcodes, principal.user_id, payload.nomenclature)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except BoardInputError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return BoardAddedResponse(added=result.added, existing=result.existing)


@router.patch("/sellers/{seller_id}/items/{barcode}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def update_item(
    seller_id: uuid.UUID,
    barcode: str,
    payload: BoardItemRequest,
    principal: CurrentPrincipal,
    service: FromDishka[BoardService],
) -> Response:
    try:
        await service.update_item(
            seller_id,
            barcode,
            note=payload.note,
            nomenclature=payload.nomenclature,
            updated_by=principal.user_id,
        )
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except BoardInputError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/sellers/{seller_id}/items/{barcode}/hidden", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def set_hidden(
    seller_id: uuid.UUID, barcode: str, payload: dict, _: CurrentPrincipal, service: FromDishka[BoardService]
) -> Response:
    try:
        await service.set_hidden(seller_id, barcode, bool(payload.get("hidden")))
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except BoardInputError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/sellers/{seller_id}/items/{barcode}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def remove_item(
    seller_id: uuid.UUID, barcode: str, _: CurrentPrincipal, service: FromDishka[BoardService]
) -> Response:
    try:
        await service.remove_item(seller_id, barcode)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except BoardInputError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/refresh", response_model=BoardRefreshResponse)
@inject
async def refresh(principal: CurrentPrincipal, service: FromDishka[BoardService]) -> BoardRefreshResponse:
    """Поставить сбор заказов по кабинетам, где есть строки. Остаток WB обновляется зеркалом отдельно."""
    return BoardRefreshResponse(queued=await service.request_refresh_all(principal.user_id))


@router.get("/export")
@inject
async def export(_: CurrentPrincipal, service: FromDishka[BoardService]) -> Response:
    report = render_board(await service.view())
    encoded = urllib.parse.quote(report.filename)
    return Response(
        content=report.content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"{report.filename}\"; filename*=UTF-8''{encoded}"},
    )
