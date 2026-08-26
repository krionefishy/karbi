import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import delete

from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import CredentialModel, OutboxEventModel, SellerModel
from backend.modules.wb_fbs_distribution.application import FbsDistributionEnrollment, FbsDistributionService
from backend.modules.wb_fbs_distribution.domain import MODE_DRY_RUN, MODE_WRITE
from backend.modules.wb_fbs_distribution.infrastructure.postgres import FbsDistributionRepository
from backend.shared.security import CredentialCipher
from backend.shared.settings import load_settings
from backend.storage.pg import Database

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")


def cipher() -> CredentialCipher:
    return CredentialCipher(SETTINGS.security.credential_encryption_keys, SETTINGS.security.credential_fingerprint_key)


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
