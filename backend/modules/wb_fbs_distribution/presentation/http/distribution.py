import uuid

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.modules.wb_fbs_distribution.application import (
    FbsDistributionService,
    MirrorService,
    SellerOverview,
)
from backend.modules.wb_fbs_distribution.presentation.http.schemas import (
    DistributionOverviewResponse,
    MirrorSyncResponse,
    ModeRequest,
    WarehouseResponse,
)

router = APIRouter(prefix="/wb/fbs", tags=["wb-fbs-distribution"])

NOT_ENROLLED = "Селлер не подключён к автоматизации"


def overview_response(overview: SellerOverview) -> DistributionOverviewResponse:
    enrollment = overview.enrollment
    return DistributionOverviewResponse(
        seller_id=enrollment.seller_id,
        mode=enrollment.mode,
        write_enabled=enrollment.write_enabled,
        enrolled_at=enrollment.enrolled_at.isoformat(),
        warehouses_synced_at=(overview.warehouses_synced_at.isoformat() if overview.warehouses_synced_at else None),
        offices_known=overview.offices_known,
        warehouses=[
            WarehouseResponse(
                warehouse_id=row.warehouse_id,
                office_id=row.office_id,
                name=row.name,
                city=row.city,
                address=row.address,
                federal_district=row.federal_district,
                cargo_type=row.cargo_type,
                is_processing=row.is_processing,
                is_deleting=row.is_deleting,
            )
            for row in overview.warehouses
        ],
    )


@router.get("/sellers/{seller_id}/overview", response_model=DistributionOverviewResponse)
@inject
async def seller_overview(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[FbsDistributionService],
) -> DistributionOverviewResponse:
    try:
        overview = await service.seller_overview(seller_id)
    except SellerNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_ENROLLED) from error
    return overview_response(overview)


@router.put("/sellers/{seller_id}/mode", response_model=DistributionOverviewResponse)
@inject
async def set_mode(
    seller_id: uuid.UUID,
    payload: ModeRequest,
    _: CurrentPrincipal,
    service: FromDishka[FbsDistributionService],
) -> DistributionOverviewResponse:
    """Разрешить или запретить автоматизации писать остатки в этот кабинет."""
    try:
        overview = await service.set_write_enabled(seller_id, payload.write_enabled)
    except SellerNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_ENROLLED) from error
    return overview_response(overview)


@router.post("/sellers/{seller_id}/sync", response_model=MirrorSyncResponse)
@inject
async def sync_mirror(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    service: FromDishka[FbsDistributionService],
    mirror: FromDishka[MirrorService],
) -> MirrorSyncResponse:
    """Сверить справочник объектов и склады кабинета сейчас, не дожидаясь суток.

    Два запроса к WB на чтение, поэтому выполняется прямо в запросе: очередь
    ради пары секунд ожидания добавила бы состояние, которое надо показывать.
    """
    try:
        await service.seller_overview(seller_id)
        result = await mirror.sync_seller(seller_id)
    except SellerNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_ENROLLED) from error
    except WBPermanentError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except WBTemporaryError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(error)) from error
    return MirrorSyncResponse(offices=result.offices, warehouses=result.warehouses)
