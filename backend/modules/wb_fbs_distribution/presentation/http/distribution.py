import uuid

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.modules.wb_fbs_distribution.application import (
    FbsDistributionService,
    InvalidPlacementError,
    MirrorService,
    PlacementService,
    QueueEntry,
    SellerOverview,
    SetupOverview,
)
from backend.modules.wb_fbs_distribution.presentation.http.schemas import (
    DistributionOverviewResponse,
    MirrorSyncResponse,
    ModeRequest,
    OfficeRegionRequest,
    OfficeResponse,
    PlacementRequest,
    QueueEntryResponse,
    RegionOrderRequest,
    RegionResponse,
    SettingsRequest,
    SetupResponse,
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
                participates=row.participates,
                position=row.position,
                region_code=row.region_code,
            )
            for row in overview.warehouses
        ],
    )


def setup_response(setup: SetupOverview) -> SetupResponse:
    return SetupResponse(
        regions=[
            RegionResponse(code=r.code, title=r.title, position=r.position, share_bp=r.share_bp) for r in setup.regions
        ],
        shares_ready=setup.shares_ready,
        reserve_units=setup.settings.reserve_units,
        priority_regions=setup.settings.priority_regions,
        offices=[
            OfficeResponse(
                office_id=o.office_id,
                name=o.name,
                city=o.city,
                address=o.address,
                federal_district=o.federal_district,
                cargo_type=o.cargo_type,
                region_code=o.region_code,
                used_by_cabinets=o.used_by_cabinets,
            )
            for o in setup.offices
        ],
        unassigned_offices=setup.unassigned_offices,
    )


def queue_response(entries: list[QueueEntry]) -> list[QueueEntryResponse]:
    return [
        QueueEntryResponse(
            place=entry.place,
            warehouse_id=entry.warehouse_id,
            name=entry.name,
            city=entry.city,
            region_code=entry.region_code,
            region_title=entry.region_title,
        )
        for entry in entries
    ]


def invalid(error: InvalidPlacementError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error))


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


@router.get("/setup", response_model=SetupResponse)
@inject
async def setup(_: CurrentPrincipal, service: FromDishka[PlacementService]) -> SetupResponse:
    """Схема распределения: направления, доли, резерв и разметка объектов WB."""
    return setup_response(await service.setup())


@router.put("/setup/regions", response_model=SetupResponse)
@inject
async def save_regions(
    payload: RegionOrderRequest,
    _: CurrentPrincipal,
    service: FromDishka[PlacementService],
) -> SetupResponse:
    """Порядок направлений задаётся порядком списка, доли — в сотых процента."""
    try:
        setup = await service.save_regions([(item.code, item.share_bp) for item in payload.regions])
    except InvalidPlacementError as error:
        raise invalid(error) from error
    return setup_response(setup)


@router.put("/setup/offices/{office_id}", response_model=SetupResponse)
@inject
async def assign_office(
    office_id: int,
    payload: OfficeRegionRequest,
    _: CurrentPrincipal,
    service: FromDishka[PlacementService],
) -> SetupResponse:
    try:
        setup = await service.assign_office(office_id, payload.region_code)
    except InvalidPlacementError as error:
        raise invalid(error) from error
    return setup_response(setup)


@router.put("/setup/settings", response_model=SetupResponse)
@inject
async def save_settings(
    payload: SettingsRequest,
    _: CurrentPrincipal,
    service: FromDishka[PlacementService],
) -> SetupResponse:
    try:
        setup = await service.save_settings(
            reserve_units=payload.reserve_units, priority_regions=payload.priority_regions
        )
    except InvalidPlacementError as error:
        raise invalid(error) from error
    return setup_response(setup)


@router.get("/sellers/{seller_id}/queue", response_model=list[QueueEntryResponse])
@inject
async def seller_queue(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    overview_service: FromDishka[FbsDistributionService],
    service: FromDishka[PlacementService],
) -> list[QueueEntryResponse]:
    """Очередь складов кабинета в том порядке, в котором их берёт расчёт."""
    try:
        await overview_service.seller_overview(seller_id)
    except SellerNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_ENROLLED) from error
    return queue_response(await service.queue(seller_id))


@router.put("/sellers/{seller_id}/warehouses/{warehouse_id}", response_model=list[QueueEntryResponse])
@inject
async def set_placement(
    seller_id: uuid.UUID,
    warehouse_id: int,
    payload: PlacementRequest,
    _: CurrentPrincipal,
    service: FromDishka[PlacementService],
) -> list[QueueEntryResponse]:
    """Участие склада в распределении и его место внутри направления."""
    try:
        entries = await service.set_placement(
            seller_id, warehouse_id, participates=payload.participates, position=payload.position
        )
    except SellerNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Склад не найден в этом кабинете") from error
    except InvalidPlacementError as error:
        raise invalid(error) from error
    return queue_response(entries)
