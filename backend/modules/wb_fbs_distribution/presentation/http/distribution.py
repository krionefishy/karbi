import uuid

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_fbs_distribution.application import FbsDistributionService, SellerOverview
from backend.modules.wb_fbs_distribution.presentation.http.schemas import (
    DistributionOverviewResponse,
    ModeRequest,
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
