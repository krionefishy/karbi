import re
import urllib.parse
import uuid

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Response, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_fbs_stocks.application import (
    XLSX_MEDIA_TYPE,
    BoardConflictError,
    BoardView,
    FbsStocksService,
    GroupView,
    RefreshRequest,
    SetupView,
)
from backend.modules.wb_fbs_stocks.presentation.http.schemas import (
    AddedResponse,
    BarcodesRequest,
    BoardResponse,
    ColumnResponse,
    GroupColumnsRequest,
    GroupOrderRequest,
    GroupRequest,
    GroupResponse,
    NoteRequest,
    RefreshAllResponse,
    RefreshResponse,
    RowResponse,
    SetupResponse,
    WarehouseSetupResponse,
)

router = APIRouter(prefix="/wb/fbs-stocks", tags=["wb-fbs-stocks"])

SPLIT = re.compile(r"[\s,;]+")


def not_enrolled() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не подключён к автоматизации")


def conflict(error: BoardConflictError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(error))


def group_response(group: GroupView) -> GroupResponse:
    return GroupResponse(
        id=group.id,
        title=group.title,
        kind=group.kind,
        columns=[ColumnResponse(warehouse_id=column.warehouse_id, name=column.name) for column in group.columns],
    )


def board_response(view: BoardView) -> BoardResponse:
    return BoardResponse(
        seller_id=view.seller_id,
        seller_name=view.seller_name,
        collected_at=view.collected_at.isoformat() if view.collected_at else None,
        collection_error=view.collection_error,
        groups=[group_response(group) for group in view.groups],
        rows=[
            RowResponse(
                barcode=row.barcode,
                note=row.note,
                article=row.article,
                title=row.title,
                vendor_code=row.vendor_code,
                in_catalog=row.in_catalog,
                amounts={str(warehouse_id): amount for warehouse_id, amount in row.amounts.items()},
                totals={str(group.id): row.group_total(group) for group in view.groups},
            )
            for row in view.rows
        ],
    )


def setup_response(view: SetupView) -> SetupResponse:
    return SetupResponse(
        seller_id=view.seller_id,
        groups=[group_response(group) for group in view.groups],
        warehouses=[
            WarehouseSetupResponse(
                warehouse_id=warehouse.warehouse_id,
                name=warehouse.name,
                delivery_type=warehouse.delivery_type,
                is_deleting=warehouse.is_deleting,
                group_id=warehouse.group_id,
                position=warehouse.position,
            )
            for warehouse in view.warehouses
        ],
    )


def refresh_response(state: RefreshRequest) -> RefreshResponse:
    return RefreshResponse(
        status=state.status,
        in_progress=state.in_progress,
        requested_at=state.requested_at.isoformat(),
        finished_at=state.finished_at.isoformat() if state.finished_at else None,
        error=state.error,
    )


@router.get("/boards", response_model=list[BoardResponse])
@inject
async def all_boards(_: CurrentPrincipal, service: FromDishka[FbsStocksService]) -> list[BoardResponse]:
    """Таблицы всех подключённых кабинетов — для листа сравнения."""
    return [board_response(view) for view in await service.views()]


@router.get("/sellers/{seller_id}", response_model=BoardResponse)
@inject
async def seller_board(
    seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[FbsStocksService]
) -> BoardResponse:
    try:
        view = await service.view(seller_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return board_response(view)


@router.get("/sellers/{seller_id}/setup", response_model=SetupResponse)
@inject
async def seller_setup(
    seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[FbsStocksService]
) -> SetupResponse:
    try:
        view = await service.setup(seller_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return setup_response(view)


@router.post("/sellers/{seller_id}/groups", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
@inject
async def add_group(
    seller_id: uuid.UUID, payload: GroupRequest, _: CurrentPrincipal, service: FromDishka[FbsStocksService]
) -> GroupResponse:
    try:
        group = await service.add_group(seller_id, payload.title, payload.kind)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except BoardConflictError as error:
        raise conflict(error) from error
    return group_response(group)


@router.put("/sellers/{seller_id}/groups/order", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def reorder_groups(
    seller_id: uuid.UUID, payload: GroupOrderRequest, _: CurrentPrincipal, service: FromDishka[FbsStocksService]
) -> Response:
    try:
        await service.reorder_groups(seller_id, payload.group_ids)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except BoardConflictError as error:
        raise conflict(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/sellers/{seller_id}/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def update_group(
    seller_id: uuid.UUID,
    group_id: uuid.UUID,
    payload: GroupRequest,
    _: CurrentPrincipal,
    service: FromDishka[FbsStocksService],
) -> Response:
    try:
        await service.update_group(seller_id, group_id, title=payload.title, kind=payload.kind)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except BoardConflictError as error:
        raise conflict(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/sellers/{seller_id}/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def delete_group(
    seller_id: uuid.UUID, group_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[FbsStocksService]
) -> Response:
    try:
        await service.delete_group(seller_id, group_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except BoardConflictError as error:
        raise conflict(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/sellers/{seller_id}/groups/{group_id}/columns", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def set_group_columns(
    seller_id: uuid.UUID,
    group_id: uuid.UUID,
    payload: GroupColumnsRequest,
    _: CurrentPrincipal,
    service: FromDishka[FbsStocksService],
) -> Response:
    """Состав группы целиком и в этом порядке; склад уходит из прежней группы сам."""
    try:
        await service.set_group_columns(seller_id, group_id, payload.warehouse_ids)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except BoardConflictError as error:
        raise conflict(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sellers/{seller_id}/barcodes", response_model=AddedResponse)
@inject
async def add_barcodes(
    seller_id: uuid.UUID, payload: BarcodesRequest, principal: CurrentPrincipal, service: FromDishka[FbsStocksService]
) -> AddedResponse:
    """Вписать баркоды: списком или вставленным текстом — по одному в строке, через запятую или пробел."""
    barcodes = [*payload.barcodes, *SPLIT.split(payload.text)]
    try:
        added = await service.add_barcodes(seller_id, barcodes, principal.user_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except BoardConflictError as error:
        raise conflict(error) from error
    return AddedResponse(added=added)


@router.delete("/sellers/{seller_id}/barcodes/{barcode}", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def remove_barcode(
    seller_id: uuid.UUID, barcode: str, _: CurrentPrincipal, service: FromDishka[FbsStocksService]
) -> Response:
    try:
        await service.remove_barcode(seller_id, barcode)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except BoardConflictError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/sellers/{seller_id}/barcodes/{barcode}/note", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def set_note(
    seller_id: uuid.UUID,
    barcode: str,
    payload: NoteRequest,
    principal: CurrentPrincipal,
    service: FromDishka[FbsStocksService],
) -> Response:
    try:
        await service.set_note(seller_id, barcode, payload.note, principal.user_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except BoardConflictError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/sellers/{seller_id}/refresh", response_model=RefreshResponse, status_code=status.HTTP_202_ACCEPTED)
@inject
async def request_refresh(
    seller_id: uuid.UUID, principal: CurrentPrincipal, service: FromDishka[FbsStocksService]
) -> RefreshResponse:
    """Собрать вне расписания. Повторное нажатие возвращает тот же запрос."""
    try:
        state = await service.request_refresh(seller_id, requested_by=principal.user_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return refresh_response(state)


@router.get("/sellers/{seller_id}/refresh", response_model=RefreshResponse | None)
@inject
async def refresh_state(
    seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[FbsStocksService]
) -> RefreshResponse | None:
    try:
        state = await service.refresh_state(seller_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return refresh_response(state) if state else None


@router.post("/refresh", response_model=RefreshAllResponse, status_code=status.HTTP_202_ACCEPTED)
@inject
async def request_refresh_all(principal: CurrentPrincipal, service: FromDishka[FbsStocksService]) -> RefreshAllResponse:
    """Обновить все кабинеты сразу — для листа сравнения."""
    return RefreshAllResponse(queued=await service.request_refresh_all(principal.user_id))


@router.get("/export")
@inject
async def export_board(_: CurrentPrincipal, service: FromDishka[FbsStocksService]) -> Response:
    """Книга в формате таблицы селлера: лист на кабинет и лист сравнения."""
    report = await service.export()
    encoded = urllib.parse.quote(report.filename)
    return Response(
        content=report.content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"{report.filename}\"; filename*=UTF-8''{encoded}"},
    )
