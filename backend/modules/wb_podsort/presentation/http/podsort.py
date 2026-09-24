import urllib.parse
from datetime import datetime

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Query, Response, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_podsort.application import XLSX_MEDIA_TYPE, PodsortQueryError, PodsortService, PodsortView
from backend.modules.wb_podsort.domain import MAX_COVER_DAYS, TARGET_REGIONS, WINDOW_CHOICES, PodsortSettings
from backend.modules.wb_podsort.presentation.http.schemas import (
    PodsortResponse,
    SellerStateResponse,
    SettingsRequest,
    SettingsResponse,
    SummaryRowResponse,
    WarehouseRegionRequest,
    WarehouseResponse,
)

router = APIRouter(prefix="/wb/podsort", tags=["wb-podsort"])


def bad_query(error: PodsortQueryError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error))


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def settings_response(settings: PodsortSettings) -> SettingsResponse:
    return SettingsResponse(
        window_days=settings.window_days,
        cover_days=settings.cover_days,
        regions=list(settings.regions),
        window_choices=list(WINDOW_CHOICES),
        max_cover_days=MAX_COVER_DAYS,
        available_regions=list(TARGET_REGIONS),
    )


def podsort_response(view: PodsortView) -> PodsortResponse:
    settings = view.settings
    rows = []
    for item in view.rows:
        figures = item.row.targets[view.region]
        info = item.row.info
        cover = figures.cover(settings.window_days)
        rows.append(
            SummaryRowResponse(
                seller_name=item.seller_name,
                nm_id=info.nm_id,
                barcode=info.barcode,
                vendor_code=info.vendor_code,
                subject=info.subject,
                tech_size=info.tech_size,
                need=item.need,
                window_orders=figures.window_orders,
                average=round(figures.average(settings.window_days), 2),
                stock=figures.stock,
                cover_days=round(cover, 1) if cover is not None else None,
            )
        )
    return PodsortResponse(
        today=view.today.isoformat(),
        last_day=view.last_day.isoformat(),
        window_start=view.window_start.isoformat(),
        settings=settings_response(settings),
        sellers=[
            SellerStateResponse(
                seller_id=str(state.seller_id),
                name=state.name,
                window_days_loaded=state.window_days_loaded,
                history_from=state.history_from.isoformat() if state.history_from else None,
                history_days_loaded=state.history_days_loaded,
                collected_at=_iso(state.collected_at),
                collection_error=state.collection_error,
                remains_at=_iso(state.remains_at),
                remains_error=state.remains_error,
            )
            for state in view.sellers
        ],
        warehouses=[
            WarehouseResponse(name=item.name, region=item.region, source=item.source, quantity=item.quantity)
            for item in view.warehouses
        ],
        region=view.region,
        rows=rows,
    )


@router.get("", response_model=PodsortResponse)
@inject
async def podsort(
    _: CurrentPrincipal,
    service: FromDishka[PodsortService],
    region: str | None = Query(default=None),
) -> PodsortResponse:
    """Расчёт по всем подключённым кабинетам: состояние сбора, склады и строки выбранного региона."""
    try:
        view = await service.view(region or None)
    except PodsortQueryError as error:
        raise bad_query(error) from error
    return podsort_response(view)


@router.put("/settings", response_model=SettingsResponse)
@inject
async def update_settings(
    payload: SettingsRequest, principal: CurrentPrincipal, service: FromDishka[PodsortService]
) -> SettingsResponse:
    try:
        settings = await service.update_settings(
            window_days=payload.window_days,
            cover_days=payload.cover_days,
            regions=payload.regions,
            updated_by=principal.user_id,
        )
    except PodsortQueryError as error:
        raise bad_query(error) from error
    return settings_response(settings)


@router.put("/warehouses", status_code=status.HTTP_204_NO_CONTENT)
@inject
async def set_warehouse_region(
    payload: WarehouseRegionRequest, _: CurrentPrincipal, service: FromDishka[PodsortService]
) -> Response:
    try:
        await service.set_warehouse_region(payload.name, payload.region, guess=payload.guess)
    except PodsortQueryError as error:
        raise bad_query(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/export")
@inject
async def export_podsort(_: CurrentPrincipal, service: FromDishka[PodsortService]) -> Response:
    report = await service.export()
    encoded = urllib.parse.quote(report.filename)
    return Response(
        content=report.content,
        media_type=XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": f"attachment; filename=\"{report.ascii_filename}\"; filename*=UTF-8''{encoded}"
        },
    )
