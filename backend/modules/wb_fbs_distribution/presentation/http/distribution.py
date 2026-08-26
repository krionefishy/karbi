import uuid

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, HTTPException, Query, Request, status

from backend.app.http.authentication import CurrentPrincipal
from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.modules.wb_fbs_distribution.application import (
    MANUAL,
    FbsDistributionService,
    InvalidPlacementError,
    InvalidShareError,
    MappingService,
    MappingState,
    MirrorService,
    PlacementService,
    Plan,
    PlanningService,
    QueueEntry,
    SellerOverview,
    SetupOverview,
    SnapshotRejected,
    SnapshotService,
    SnapshotState,
)
from backend.modules.wb_fbs_distribution.infrastructure.onec import SnapshotFormatError, parse_snapshot
from backend.modules.wb_fbs_distribution.infrastructure.postgres import FbsDistributionRepository
from backend.modules.wb_fbs_distribution.presentation.http.schemas import (
    DistributionOverviewResponse,
    MappingStateResponse,
    MatchResponse,
    MirrorSyncResponse,
    ModeRequest,
    OfficeRegionRequest,
    OfficeResponse,
    PlacementRequest,
    PlanAmountResponse,
    PlanItemResponse,
    PlanResponse,
    PlanSkipResponse,
    PoolResponse,
    PoolShareRequest,
    QueueEntryResponse,
    RegionOrderRequest,
    RegionResponse,
    SettingsRequest,
    SetupResponse,
    SharedPoolResponse,
    SnapshotHistoryItem,
    SnapshotStateResponse,
    UnmappedPoolResponse,
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
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))


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


def snapshot_response(state: SnapshotState, reserve_units: int) -> SnapshotStateResponse:
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


@router.get("/stock", response_model=SnapshotStateResponse)
@inject
async def stock_state(
    _: CurrentPrincipal,
    snapshots: FromDishka[SnapshotService],
    placement: FromDishka[PlacementService],
) -> SnapshotStateResponse:
    """Чем сейчас можно считать: какой снимок принят и насколько он свежий."""
    setup = await placement.setup()
    return snapshot_response(await snapshots.state(), setup.settings.reserve_units)


@router.post("/stock", response_model=SnapshotStateResponse)
@inject
async def upload_stock(
    request: Request,
    _: CurrentPrincipal,
    snapshots: FromDishka[SnapshotService],
    placement: FromDishka[PlacementService],
) -> SnapshotStateResponse:
    """Принять абсолютный снимок остатков 1С: тело запроса — CSV или JSON.

    Пока обмена с 1С нет, снимок грузит оператор файлом. Тем же телом сможет
    ходить и будущий адаптер: сырое тело, а не форма, чтобы у обмена с 1С не
    было лишнего обёртывания.
    """
    payload = await request.body()
    try:
        snapshot = parse_snapshot(payload)
    except SnapshotFormatError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    try:
        state = await snapshots.accept(snapshot, source=MANUAL)
    except SnapshotRejected as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    setup = await placement.setup()
    return snapshot_response(state, setup.settings.reserve_units)


@router.get("/stock/history", response_model=list[SnapshotHistoryItem])
@inject
async def stock_history(
    _: CurrentPrincipal,
    repository: FromDishka[FbsDistributionRepository],
) -> list[SnapshotHistoryItem]:
    """Журнал выгрузок, включая отклонённые: битый файл — тоже событие."""
    return [
        SnapshotHistoryItem(
            id=row.id,
            source=row.source,
            generated_at=row.generated_at.isoformat(),
            received_at=row.received_at.isoformat(),
            lines=row.lines,
            status=row.status,
            error=row.error,
        )
        for row in await repository.snapshot_history()
    ]


@router.get("/stock/pools", response_model=list[PoolResponse])
@inject
async def stock_pools(
    _: CurrentPrincipal,
    snapshots: FromDishka[SnapshotService],
    search: str = Query("", max_length=128),
) -> list[PoolResponse]:
    return [
        PoolResponse(
            item_id=pool.item_id,
            characteristic=pool.characteristic,
            barcode=pool.barcode,
            name=pool.name,
            on_hand=pool.on_hand,
            available=pool.available,
        )
        for pool in await snapshots.pools(search=search)
    ]


def mapping_response(state: MappingState) -> MappingStateResponse:
    return MappingStateResponse(
        pools=state.pools,
        mapped_pools=state.mapped_pools,
        shared_without_rule=state.shared_without_rule,
        unmapped=[
            UnmappedPoolResponse(
                item_id=pool.item_id,
                characteristic=pool.characteristic,
                barcode=pool.barcode,
                name=pool.name,
                on_hand=pool.on_hand,
            )
            for pool in state.unmapped
        ],
        shared=[
            SharedPoolResponse(
                item_id=pool.item_id,
                characteristic=pool.characteristic,
                barcode=pool.barcode,
                name=pool.name,
                on_hand=pool.on_hand,
                sellers=pool.sellers,
                shares=pool.shares,
                rule_ready=pool.rule_ready,
            )
            for pool in state.shared
        ],
    )


@router.get("/mapping", response_model=MappingStateResponse)
@inject
async def mapping_state(_: CurrentPrincipal, service: FromDishka[MappingService]) -> MappingStateResponse:
    """Что сопоставилось с карточками WB, что нет и что делят несколько кабинетов."""
    return mapping_response(await service.state())


@router.post("/sellers/{seller_id}/mapping", response_model=MatchResponse)
@inject
async def rematch(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    overview_service: FromDishka[FbsDistributionService],
    service: FromDishka[MappingService],
) -> MatchResponse:
    """Пересобрать связи кабинета по текущему каталогу WB и текущему снимку 1С."""
    try:
        await overview_service.seller_overview(seller_id)
    except SellerNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_ENROLLED) from error
    result = await service.rematch(seller_id)
    return MatchResponse(matched=result.matched, catalog_sizes=result.catalog_sizes, pools=result.pools)


@router.put("/mapping/shares", response_model=MappingStateResponse)
@inject
async def set_pool_shares(
    payload: PoolShareRequest,
    _: CurrentPrincipal,
    service: FromDishka[MappingService],
) -> MappingStateResponse:
    """Как один физический пул делится между кабинетами."""
    try:
        state = await service.set_shares(payload.item_id, payload.characteristic, payload.shares)
    except InvalidShareError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return mapping_response(state)


@router.post("/sellers/{seller_id}/plan", response_model=PlanResponse)
@inject
async def build_plan(
    seller_id: uuid.UUID,
    _: CurrentPrincipal,
    overview_service: FromDishka[FbsDistributionService],
    placement: FromDishka[PlacementService],
    planning: FromDishka[PlanningService],
) -> PlanResponse:
    """Посчитать распределение по кабинету. В Wildberries ничего не уходит."""
    try:
        await overview_service.seller_overview(seller_id)
    except SellerNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_ENROLLED) from error
    plan = await planning.build(seller_id)
    return await plan_response(plan, placement)


async def plan_response(plan: Plan, placement: PlacementService) -> PlanResponse:
    queue = {entry.warehouse_id: entry for entry in await placement.queue(plan.seller_id)}
    order = {warehouse_id: index for index, warehouse_id in enumerate(queue)}
    return PlanResponse(
        id=plan.id,
        seller_id=plan.seller_id,
        snapshot_id=plan.snapshot_id,
        created_at=plan.created_at.isoformat(),
        reserve_units=plan.reserve_units,
        priority_regions=plan.priority_regions,
        warehouses=plan.warehouses,
        units=plan.units,
        items=[
            PlanItemResponse(
                chrt_id=item.chrt_id,
                item_id=item.item_id,
                characteristic=item.characteristic,
                name=item.name,
                barcode=item.barcode,
                on_hand=item.on_hand,
                available=item.available,
                units=item.units,
                amounts=[
                    PlanAmountResponse(
                        warehouse_id=warehouse_id,
                        name=queue[warehouse_id].name if warehouse_id in queue else "",
                        city=queue[warehouse_id].city if warehouse_id in queue else "",
                        region_code=queue[warehouse_id].region_code if warehouse_id in queue else None,
                        amount=amount,
                    )
                    # В порядке очереди распределения, а не по номеру склада:
                    # оператор читает план сверху вниз как приоритет.
                    for warehouse_id, amount in sorted(
                        item.amounts.items(), key=lambda pair: order.get(pair[0], len(order))
                    )
                ],
            )
            for item in plan.items
        ],
        skips=[
            PlanSkipResponse(
                chrt_id=skip.chrt_id,
                item_id=skip.item_id,
                characteristic=skip.characteristic,
                name=skip.name,
                reason=skip.reason,
                text=skip.text,
            )
            for skip in plan.skips
        ],
    )
