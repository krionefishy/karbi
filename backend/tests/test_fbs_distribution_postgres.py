import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy import update as sa_update

from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import CredentialModel, OutboxEventModel, SellerModel
from backend.modules.wb_fbs_distribution.application import (
    FbsDistributionEnrollment,
    FbsDistributionService,
    InvalidPlacementError,
    MirrorService,
    PlacementService,
)
from backend.modules.wb_fbs_distribution.domain import (
    BASIS_POINTS,
    DEFAULT_REGIONS,
    MODE_DRY_RUN,
    MODE_WRITE,
)
from backend.modules.wb_fbs_distribution.infrastructure.postgres import (
    FbsDistributionRepository,
    OfficeRegionModel,
    RegionModel,
    WBOfficeModel,
)
from backend.modules.wb_fbs_distribution.infrastructure.wb import Office, SellerWarehouse, WBFbsMarketplaceClient
from backend.shared.security import CredentialCipher
from backend.shared.settings import load_settings
from backend.storage.pg import Database

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")


def cipher() -> CredentialCipher:
    return CredentialCipher(SETTINGS.security.credential_encryption_keys, SETTINGS.security.credential_fingerprint_key)


def office(office_id: int, city: str = "Москва", cargo_type: int = 1) -> Office:
    return Office(
        office_id=office_id,
        name=f"{city} ({office_id})",
        city=city,
        address=f"РФ, {city}",
        federal_district="Центральный федеральный округ",
        longitude=37.6,
        latitude=55.7,
        cargo_type=cargo_type,
        delivery_type=1,
        selected=True,
    )


def warehouse(warehouse_id: int, office_id: int, *, processing: bool = False) -> SellerWarehouse:
    return SellerWarehouse(
        warehouse_id=warehouse_id,
        office_id=office_id,
        store_id=warehouse_id * 10,
        name=f"Склад {warehouse_id}",
        cargo_type=1,
        delivery_type=1,
        is_deleting=False,
        is_processing=processing,
    )


class FakeMarketplace(WBFbsMarketplaceClient):
    def __init__(self, offices=(), warehouses=()) -> None:
        super().__init__()
        self.office_rows = list(offices)
        self.warehouse_rows = list(warehouses)

    async def offices(self, api_key: str):
        return list(self.office_rows)

    async def warehouses(self, api_key: str):
        return list(self.warehouse_rows)


def mirror(session, marketplace: FakeMarketplace) -> MirrorService:
    return MirrorService(
        session,
        SellerRepository(session),
        FbsDistributionRepository(session),
        marketplace,
        cipher(),
    )


@pytest_asyncio.fixture
async def seller() -> AsyncIterator[tuple[Database, uuid.UUID]]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    model = SellerModel(name="FBS распределение тест", catalog_sync_status="success")
    async with database.session() as session:
        session.add(model)
        await session.flush()
        session.add(
            CredentialModel(
                seller_id=model.id,
                encrypted_api_key=cipher().encrypt("wb-fbs-key"),
                key_fingerprint=uuid.uuid4().hex,
            )
        )
        await session.commit()
    try:
        yield database, model.id
    finally:
        async with database.session() as session:
            await FbsDistributionRepository(session).purge_seller(model.id)
            # Справочник объектов общий, не по селлеру: без уборки он утёк бы
            # в следующий тест и подменил бы то, что тот проверяет.
            await session.execute(delete(WBOfficeModel))
            await session.execute(delete(OfficeRegionModel))
            # Порядок и доли направлений общие; тест, который их менял, не должен
            # решать за следующий, с чего тот начнёт.
            for position, (code, _) in enumerate(DEFAULT_REGIONS):
                await session.execute(
                    sa_update(RegionModel).where(RegionModel.code == code).values(position=position, share_bp=0)
                )
            await FbsDistributionRepository(session).save_settings(reserve_units=20, priority_regions=3)
            await session.execute(delete(OutboxEventModel).where(OutboxEventModel.aggregate_id == model.id))
            await session.execute(delete(SellerModel).where(SellerModel.id == model.id))
            await session.commit()
        await database.disconnect()


async def test_enrolling_twice_is_not_an_error(seller) -> None:
    """The registry may replay an attach; the second one must be a no-op."""
    database, seller_id = seller
    async with database.session() as session:
        enrollment = FbsDistributionEnrollment(FbsDistributionRepository(session))
        await enrollment.attach(seller_id)
        await enrollment.attach(seller_id)
        await session.commit()

    async with database.session() as session:
        assert seller_id in await FbsDistributionEnrollment(FbsDistributionRepository(session)).seller_ids()


async def test_a_new_cabinet_may_not_write_to_wb(seller) -> None:
    """Connecting a cabinet must never by itself grant the right to rewrite its stock."""
    database, seller_id = seller
    async with database.session() as session:
        distribution = FbsDistributionRepository(session)
        await FbsDistributionEnrollment(distribution).attach(seller_id)
        await session.commit()

    async with database.session() as session:
        state = await FbsDistributionRepository(session).enrollment(seller_id)
        assert state is not None
        assert state.write_enabled is False
        assert state.mode == MODE_DRY_RUN


async def test_write_mode_is_switched_per_cabinet(seller) -> None:
    database, seller_id = seller
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()

    async with database.session() as session:
        service = FbsDistributionService(session, SellerRepository(session), FbsDistributionRepository(session))
        overview = await service.set_write_enabled(seller_id, True)
        assert overview.enrollment.mode == MODE_WRITE

    async with database.session() as session:
        service = FbsDistributionService(session, SellerRepository(session), FbsDistributionRepository(session))
        overview = await service.set_write_enabled(seller_id, False)
        assert overview.enrollment.mode == MODE_DRY_RUN


async def test_an_unconnected_cabinet_has_no_mode_to_switch(seller) -> None:
    database, _ = seller
    async with database.session() as session:
        service = FbsDistributionService(session, SellerRepository(session), FbsDistributionRepository(session))
        with pytest.raises(SellerNotFoundError):
            await service.set_write_enabled(uuid.uuid4(), True)


async def test_detaching_keeps_the_seller_and_purging_clears_the_module(seller) -> None:
    database, seller_id = seller
    async with database.session() as session:
        enrollment = FbsDistributionEnrollment(FbsDistributionRepository(session))
        await enrollment.attach(seller_id)
        await session.commit()

    async with database.session() as session:
        enrollment = FbsDistributionEnrollment(FbsDistributionRepository(session))
        await enrollment.detach(seller_id)
        await session.commit()

    async with database.session() as session:
        distribution = FbsDistributionRepository(session)
        assert await distribution.enrollment(seller_id) is None
        assert await SellerRepository(session).get(seller_id) is not None


async def test_the_catalog_counts_only_connected_cabinets(seller) -> None:
    database, seller_id = seller
    async with database.session() as session:
        service = FbsDistributionService(session, SellerRepository(session), FbsDistributionRepository(session))
        before = (await service.overview()).seller_count
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()

    async with database.session() as session:
        service = FbsDistributionService(session, SellerRepository(session), FbsDistributionRepository(session))
        assert (await service.overview()).seller_count == before + 1


async def test_a_sync_mirrors_both_the_catalogue_and_the_cabinet(seller) -> None:
    database, seller_id = seller
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()

    marketplace = FakeMarketplace(
        offices=[office(3103350), office(204, "Краснодар")],
        warehouses=[warehouse(2035130, 3103350)],
    )
    async with database.session() as session:
        result = await mirror(session, marketplace).sync_seller(seller_id)
    assert (result.offices, result.warehouses) == (2, 1)

    async with database.session() as session:
        distribution = FbsDistributionRepository(session)
        assert {row.office_id for row in await distribution.offices()} == {3103350, 204}
        [row] = await distribution.warehouses(seller_id)
        assert (row.warehouse_id, row.office_id, row.store_id) == (2035130, 3103350, 20351300)
        tracked = await distribution.tracked_row(seller_id)
        assert tracked is not None and tracked.warehouses_synced_at is not None


async def test_a_warehouse_gone_from_wb_leaves_the_mirror(seller) -> None:
    """Keeping it would let the next calculation put stock on a warehouse the
    cabinet no longer has."""
    database, seller_id = seller
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()

    async with database.session() as session:
        await mirror(
            session, FakeMarketplace(offices=[office(1)], warehouses=[warehouse(10, 1), warehouse(11, 1)])
        ).sync_seller(seller_id)
    async with database.session() as session:
        await mirror(session, FakeMarketplace(offices=[office(1)], warehouses=[warehouse(10, 1)])).sync_seller(
            seller_id
        )

    async with database.session() as session:
        rows = await FbsDistributionRepository(session).warehouses(seller_id)
        assert [row.warehouse_id for row in rows] == [10]


async def test_an_office_missing_from_the_answer_is_kept(seller) -> None:
    """Warehouses point at offices; dropping one because a single answer omitted
    it would blank the address of a warehouse that still works."""
    database, seller_id = seller
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()

    async with database.session() as session:
        await mirror(session, FakeMarketplace(offices=[office(1), office(2)], warehouses=[])).sync_seller(seller_id)
    async with database.session() as session:
        await mirror(session, FakeMarketplace(offices=[office(1)], warehouses=[])).sync_seller(seller_id)

    async with database.session() as session:
        assert {row.office_id for row in await FbsDistributionRepository(session).offices()} == {1, 2}


async def test_a_cabinet_detached_mid_walk_is_not_resurrected(seller) -> None:
    database, seller_id = seller

    async with database.session() as session:
        result = await mirror(session, FakeMarketplace(offices=[office(1)], warehouses=[warehouse(10, 1)])).sync_seller(
            seller_id
        )

    assert (result.offices, result.warehouses) == (0, 0)
    async with database.session() as session:
        assert await FbsDistributionRepository(session).warehouses(seller_id) == []


async def test_the_overview_pairs_a_warehouse_with_its_office(seller) -> None:
    database, seller_id = seller
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()
    async with database.session() as session:
        await mirror(
            session,
            FakeMarketplace(offices=[office(204, "Краснодар")], warehouses=[warehouse(2085061, 204, processing=True)]),
        ).sync_seller(seller_id)

    async with database.session() as session:
        service = FbsDistributionService(session, SellerRepository(session), FbsDistributionRepository(session))
        overview = await service.seller_overview(seller_id)

    [row] = overview.warehouses
    assert (row.city, row.federal_district) == ("Краснодар", "Центральный федеральный округ")
    assert row.is_processing is True
    assert overview.warehouses_synced_at is not None


async def test_a_warehouse_on_an_unknown_office_still_shows_up(seller) -> None:
    """The two answers are independent; a warehouse must not vanish because the
    catalogue this key returned did not mention its office."""
    database, seller_id = seller
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()
    async with database.session() as session:
        await mirror(session, FakeMarketplace(offices=[], warehouses=[warehouse(10, 999_999)])).sync_seller(seller_id)

    async with database.session() as session:
        service = FbsDistributionService(session, SellerRepository(session), FbsDistributionRepository(session))
        overview = await service.seller_overview(seller_id)

    [row] = overview.warehouses
    assert (row.warehouse_id, row.office_id, row.city) == (10, 999_999, "")


async def test_only_cabinets_whose_mirror_went_stale_are_due(seller) -> None:
    database, seller_id = seller
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()

    async with database.session() as session:
        distribution = FbsDistributionRepository(session)
        assert seller_id in await distribution.sellers_due_for_sync(datetime.now(UTC))

    async with database.session() as session:
        await mirror(session, FakeMarketplace(offices=[office(1)], warehouses=[warehouse(10, 1)])).sync_seller(
            seller_id
        )

    async with database.session() as session:
        distribution = FbsDistributionRepository(session)
        assert seller_id not in await distribution.sellers_due_for_sync(datetime.now(UTC) - timedelta(hours=1))
        assert seller_id in await distribution.sellers_due_for_sync(datetime.now(UTC) + timedelta(hours=1))


async def test_purging_a_cabinet_takes_its_warehouses(seller) -> None:
    database, seller_id = seller
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()
    async with database.session() as session:
        await mirror(session, FakeMarketplace(offices=[office(1)], warehouses=[warehouse(10, 1)])).sync_seller(
            seller_id
        )

    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).purge(seller_id)
        await session.commit()

    async with database.session() as session:
        distribution = FbsDistributionRepository(session)
        assert await distribution.warehouses(seller_id) == []
        # Справочник объектов общий и переживает отключение кабинета.
        assert {row.office_id for row in await distribution.offices()} == {1}


async def placement(session) -> PlacementService:
    return PlacementService(session, FbsDistributionRepository(session))


async def test_directions_arrive_already_ordered_and_without_invented_shares(seller) -> None:
    """The order is confirmed business; the percentages are not, and a made-up
    share would quietly hand stock to the wrong region."""
    database, _ = seller
    async with database.session() as session:
        setup = await (await placement(session)).setup()

    assert [region.code for region in setup.regions] == [code for code, _ in DEFAULT_REGIONS]
    assert all(region.share_bp == 0 for region in setup.regions)
    assert setup.shares_ready is False
    assert (setup.settings.reserve_units, setup.settings.priority_regions) == (20, 3)


async def test_shares_are_kept_only_when_they_make_a_whole_hundred(seller) -> None:
    database, _ = seller
    codes = [code for code, _ in DEFAULT_REGIONS]

    async with database.session() as session:
        service = await placement(session)
        with pytest.raises(InvalidPlacementError):
            await service.save_regions([(code, 1000) for code in codes])

    async with database.session() as session:
        service = await placement(session)
        setup = await service.save_regions(list(zip(codes, (4000, 2000, 1200, 1300, 1000, 500), strict=True)))

    assert setup.shares_ready is True
    assert [region.share_bp for region in setup.regions] == [4000, 2000, 1200, 1300, 1000, 500]


async def test_reordering_directions_keeps_their_shares(seller) -> None:
    database, _ = seller
    codes = [code for code, _ in DEFAULT_REGIONS]
    async with database.session() as session:
        service = await placement(session)
        await service.save_regions(list(zip(codes, (4000, 2000, 1200, 1300, 1000, 500), strict=True)))

    async with database.session() as session:
        service = await placement(session)
        setup = await service.save_regions(list(zip(reversed(codes), (500, 1000, 1300, 1200, 2000, 4000), strict=True)))

    assert [region.code for region in setup.regions] == list(reversed(codes))
    assert setup.shares_ready is True


async def test_a_direction_cannot_be_invented_or_dropped(seller) -> None:
    database, _ = seller
    async with database.session() as session:
        service = await placement(session)
        with pytest.raises(InvalidPlacementError):
            await service.save_regions([("moscow", BASIS_POINTS)])
        with pytest.raises(InvalidPlacementError):
            await service.save_regions([(code, 0) for code, _ in DEFAULT_REGIONS] + [("mars", 0)])


async def test_marking_an_office_survives_a_sync_with_wb(seller) -> None:
    """The mirror is overwritten by whatever WB answers; the operator's own
    marking is ours and must outlive it."""
    database, seller_id = seller
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()
    async with database.session() as session:
        await mirror(session, FakeMarketplace(offices=[office(204, "Краснодар")], warehouses=[])).sync_seller(seller_id)

    async with database.session() as session:
        setup = await (await placement(session)).assign_office(204, "krasnodar")
    assert setup.unassigned_offices == 0

    async with database.session() as session:
        await mirror(session, FakeMarketplace(offices=[office(204, "Краснодар")], warehouses=[])).sync_seller(seller_id)

    async with database.session() as session:
        assert (await FbsDistributionRepository(session).office_regions())[204] == "krasnodar"


async def test_the_queue_walks_the_directions_in_turn(seller) -> None:
    database, seller_id = seller
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()
    async with database.session() as session:
        await mirror(
            session,
            FakeMarketplace(
                offices=[office(1, "Москва"), office(2, "Москва"), office(3, "Казань")],
                warehouses=[warehouse(10, 1), warehouse(11, 2), warehouse(12, 3)],
            ),
        ).sync_seller(seller_id)
    async with database.session() as session:
        service = await placement(session)
        await service.assign_office(1, "moscow")
        await service.assign_office(2, "moscow")
        await service.assign_office(3, "volga")

    async with database.session() as session:
        entries = await (await placement(session)).queue(seller_id)

    assert [entry.warehouse_id for entry in entries] == [10, 12, 11]
    assert [entry.region_title for entry in entries] == ["Москва", "Приволжье", "Москва"]


async def test_a_warehouse_taken_out_of_the_scheme_leaves_the_queue(seller) -> None:
    database, seller_id = seller
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()
    async with database.session() as session:
        await mirror(
            session,
            FakeMarketplace(offices=[office(1)], warehouses=[warehouse(10, 1), warehouse(11, 1)]),
        ).sync_seller(seller_id)
    async with database.session() as session:
        await (await placement(session)).assign_office(1, "moscow")

    async with database.session() as session:
        entries = await (await placement(session)).set_placement(seller_id, 11, participates=False, position=0)

    assert [entry.warehouse_id for entry in entries] == [10]


async def test_the_operator_order_survives_a_sync_with_wb(seller) -> None:
    database, seller_id = seller
    async with database.session() as session:
        await FbsDistributionEnrollment(FbsDistributionRepository(session)).attach(seller_id)
        await session.commit()
    rows = FakeMarketplace(offices=[office(1)], warehouses=[warehouse(10, 1), warehouse(11, 1)])
    async with database.session() as session:
        await mirror(session, rows).sync_seller(seller_id)
    async with database.session() as session:
        await (await placement(session)).assign_office(1, "moscow")
    async with database.session() as session:
        await (await placement(session)).set_placement(seller_id, 11, participates=True, position=0)
        await (await placement(session)).set_placement(seller_id, 10, participates=True, position=1)

    async with database.session() as session:
        await mirror(session, rows).sync_seller(seller_id)

    async with database.session() as session:
        entries = await (await placement(session)).queue(seller_id)
    assert [entry.warehouse_id for entry in entries] == [11, 10]


async def test_settings_are_checked_before_they_reach_the_calculation(seller) -> None:
    database, _ = seller
    async with database.session() as session:
        service = await placement(session)
        with pytest.raises(InvalidPlacementError):
            await service.save_settings(reserve_units=-1, priority_regions=3)
        with pytest.raises(InvalidPlacementError):
            await service.save_settings(reserve_units=20, priority_regions=0)

    async with database.session() as session:
        setup = await (await placement(session)).save_settings(reserve_units=15, priority_regions=4)
    assert (setup.settings.reserve_units, setup.settings.priority_regions) == (15, 4)
