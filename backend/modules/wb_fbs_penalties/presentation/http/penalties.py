import re
import urllib.parse
import uuid
from datetime import date, datetime, timedelta

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Query, Response, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_fbs_penalties.application import (
    XLSX_MEDIA_TYPE,
    PenaltiesQueryError,
    PenaltiesService,
    PenaltiesView,
    PenaltyRowView,
    RefreshRequest,
)
from backend.modules.wb_fbs_penalties.presentation.http.schemas import (
    GroupTotalResponse,
    LookupMissResponse,
    LookupRequest,
    LookupResponse,
    PenaltiesResponse,
    PenaltyRowResponse,
    RefreshResponse,
    WarehouseOptionResponse,
)

router = APIRouter(prefix="/wb/fbs-penalties", tags=["wb-fbs-penalties"])

SPLIT = re.compile(r"[\s,;]+")
DEFAULT_PERIOD_DAYS = 7
DEFAULT_PAGE_SIZE = 200


def not_enrolled() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Селлер не подключён к автоматизации")


def bad_query(error: PenaltiesQueryError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error))


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def row_response(item: PenaltyRowView) -> PenaltyRowResponse:
    row = item.row
    return PenaltyRowResponse(
        rrd_id=row.rrd_id,
        report_period=f"{row.date_from.isoformat()}/{row.date_to.isoformat()}",
        barcode=row.barcode,
        nm_id=row.nm_id,
        title=row.sa_name,
        kind=row.bonus_type_name,
        group=item.group,
        group_title=item.group_title,
        amount=row.amount,
        sticker_id=str(row.sticker_id) if row.sticker_id else "",
        srid=row.srid,
        assembly_id=str(row.assembly_id) if row.assembly_id else "",
        order_dt=_iso(item.order_created_at or row.order_dt),
        trace=item.trace,
        warehouse_name=item.warehouse_name,
        warehouse_id=item.warehouse_id,
        supply_id=item.supply_id,
        supply_created_at=_iso(item.supply_created_at),
        supply_scan_dt=_iso(item.supply_scan_dt),
        destination_office_name=item.destination_office_name,
    )


def penalties_response(view: PenaltiesView) -> PenaltiesResponse:
    return PenaltiesResponse(
        seller_id=str(view.seller_id),
        seller_name=view.seller_name,
        date_from=view.date_from.isoformat(),
        date_to=view.date_to.isoformat(),
        collected_at=_iso(view.collected_at),
        collection_error=view.collection_error,
        rows=[row_response(item) for item in view.rows],
        totals=[GroupTotalResponse(group=t.group, title=t.title, count=t.count, amount=t.amount) for t in view.totals],
        warehouses=[WarehouseOptionResponse(warehouse_id=w.warehouse_id, name=w.name) for w in view.warehouses],
        page=view.page,
        page_size=view.page_size or max(len(view.rows), 1),
        total_rows=view.total_rows,
    )


def refresh_response(state: RefreshRequest) -> RefreshResponse:
    return RefreshResponse(
        status=state.status,
        in_progress=state.in_progress,
        requested_at=state.requested_at.isoformat(),
        finished_at=_iso(state.finished_at),
        error=state.error,
    )


def _period(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    end = date_to or date.today()
    start = date_from or end - timedelta(days=DEFAULT_PERIOD_DAYS)
    return start, end


@router.get("/sellers/{seller_id}", response_model=PenaltiesResponse)
@inject
async def seller_penalties(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[PenaltiesService],
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    group: str | None = Query(default=None),
    warehouse: int | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> PenaltiesResponse:
    start, end = _period(date_from, date_to)
    try:
        view = await service.view(
            seller_id, start, end, group=group or None, warehouse_id=warehouse, page=page, page_size=page_size
        )
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except PenaltiesQueryError as error:
        raise bad_query(error) from error
    return penalties_response(view)


@router.post("/sellers/{seller_id}/lookup", response_model=LookupResponse)
@inject
async def lookup(
    seller_id: uuid.UUID, payload: LookupRequest, _: CurrentPrincipal, service: FromDishka[PenaltiesService]
) -> LookupResponse:
    """Вставленные стикеры или номера заказов — по одному в строке или через запятую."""
    keys = [key for text in payload.keys for key in SPLIT.split(text) if key]
    try:
        view = await service.lookup(seller_id, keys)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except PenaltiesQueryError as error:
        raise bad_query(error) from error
    return LookupResponse(
        rows=[row_response(item) for item in view.rows],
        missing=[LookupMissResponse(key=miss.key, reason=miss.reason) for miss in view.missing],
    )


@router.get("/sellers/{seller_id}/export")
@inject
async def export_penalties(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[PenaltiesService],
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
) -> Response:
    start, end = _period(date_from, date_to)
    try:
        report = await service.export(seller_id, start, end)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    except PenaltiesQueryError as error:
        raise bad_query(error) from error
    encoded = urllib.parse.quote(report.filename)
    return Response(
        content=report.content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"{report.filename}\"; filename*=UTF-8''{encoded}"},
    )


@router.post("/sellers/{seller_id}/refresh", response_model=RefreshResponse, status_code=status.HTTP_202_ACCEPTED)
@inject
async def request_refresh(
    seller_id: uuid.UUID, principal: CurrentPrincipal, service: FromDishka[PenaltiesService]
) -> RefreshResponse:
    """Собрать отчёт вне расписания. Повторное нажатие возвращает тот же запрос."""
    try:
        state = await service.request_refresh(seller_id, requested_by=principal.user_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return refresh_response(state)


@router.get("/sellers/{seller_id}/refresh", response_model=RefreshResponse | None)
@inject
async def refresh_state(
    seller_id: uuid.UUID, _: CurrentPrincipal, service: FromDishka[PenaltiesService]
) -> RefreshResponse | None:
    try:
        state = await service.refresh_state(seller_id)
    except SellerNotFoundError as error:
        raise not_enrolled() from error
    return refresh_response(state) if state else None
