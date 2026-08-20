import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from backend.app.application import Application
from backend.modules.wb_core.infrastructure.postgres.models import CredentialModel, OutboxEventModel, SellerModel
from backend.modules.wb_reviews.infrastructure.postgres.models import DailyReviewCountModel, TrackedSellerModel
from backend.shared.settings import load_settings

API = "/api/v1"


@pytest_asyncio.fixture
async def registry() -> AsyncIterator[tuple[Application, AsyncClient]]:
    application = Application(load_settings("backend/shared/settings/config.test.yaml"))
    app = application.get_app()
    async with app.router.lifespan_context(app):
        token = application.token_service.issue_access(uuid.uuid4())
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            try:
                yield application, client
            finally:
                async with application.database.session() as session:
                    await session.execute(delete(DailyReviewCountModel))
                    await session.execute(delete(TrackedSellerModel))
                    await session.execute(delete(OutboxEventModel))
                    await session.execute(delete(SellerModel))
                    await session.commit()


async def create_seller(client: AsyncClient, name: str, key: str) -> dict:
    response = await client.post(f"{API}/wb/sellers", json={"name": name, "api_key": key})
    assert response.status_code == 201, response.text
    return response.json()


async def test_a_new_seller_belongs_to_no_automation(registry) -> None:
    _, client = registry

    created = await create_seller(client, "Реестр Ромашка", "wb-registry-key-1")

    listed = (await client.get(f"{API}/wb/sellers")).json()
    assert [item["id"] for item in listed] == [created["id"]]
    assert listed[0]["automations"] == []
    assert (await client.get(f"{API}/automations/wb-reviews/sellers")).json() == []


async def test_leaving_an_automation_keeps_the_seller_and_his_history(registry) -> None:
    application, client = registry
    seller = await create_seller(client, "Реестр Отзывы", "wb-registry-key-2")
    seller_id = uuid.UUID(seller["id"])

    attached = await client.post(f"{API}/automations/wb-reviews/sellers", json={"seller_id": str(seller_id)})
    assert attached.status_code == 201
    assert attached.json()["automations"] == ["wb-reviews"]

    async with application.database.session() as session:
        session.add(DailyReviewCountModel(seller_id=seller_id, article="123", date=date(2026, 8, 18), count_rating_5=7))
        await session.commit()

    detached = await client.delete(f"{API}/automations/wb-reviews/sellers/{seller_id}")
    assert detached.status_code == 204

    listed = (await client.get(f"{API}/wb/sellers")).json()
    assert [item["id"] for item in listed] == [str(seller_id)]
    assert listed[0]["automations"] == []
    async with application.database.session() as session:
        kept = await session.scalar(select(DailyReviewCountModel).where(DailyReviewCountModel.seller_id == seller_id))
        assert kept is not None and kept.count_rating_5 == 7


async def test_archiving_releases_the_key_and_hides_the_seller(registry) -> None:
    application, client = registry
    seller = await create_seller(client, "Реестр Архив", "wb-registry-key-3")
    seller_id = uuid.UUID(seller["id"])
    await client.post(f"{API}/automations/wb-reviews/sellers", json={"seller_id": str(seller_id)})

    assert (await client.delete(f"{API}/wb/sellers/{seller_id}")).status_code == 204

    assert (await client.get(f"{API}/wb/sellers")).json() == []
    archived = (await client.get(f"{API}/wb/sellers", params={"include_archived": True})).json()
    assert archived[0]["archived_at"] is not None
    assert archived[0]["automations"] == []
    assert (await client.get(f"{API}/automations/wb-reviews/sellers")).json() == []
    async with application.database.session() as session:
        credential = await session.scalar(select(CredentialModel).where(CredentialModel.seller_id == seller_id))
        assert credential is None
    # The archived seller may not be collected for, and the same key can be
    # handed to a new one because archiving let go of the fingerprint.
    assert (await client.post(f"{API}/wb/sellers/{seller_id}/catalog-sync")).status_code == 409
    assert (
        await client.post(f"{API}/automations/wb-reviews/sellers", json={"seller_id": str(seller_id)})
    ).status_code == 409
    reused = await client.post(f"{API}/wb/sellers", json={"name": "Другой", "api_key": "wb-registry-key-3"})
    assert reused.status_code == 201


async def test_restoring_brings_the_seller_back_with_a_new_key(registry) -> None:
    _, client = registry
    seller = await create_seller(client, "Реестр Возврат", "wb-registry-key-4")
    seller_id = seller["id"]
    await client.delete(f"{API}/wb/sellers/{seller_id}")

    restored = await client.post(f"{API}/wb/sellers/{seller_id}/restore", json={"api_key": "wb-registry-key-4-new"})

    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None
    assert restored.json()["catalog_sync_status"] == "queued"
    assert [item["id"] for item in (await client.get(f"{API}/wb/sellers")).json()] == [seller_id]


async def test_purge_erases_the_history_every_automation_collected(registry) -> None:
    application, client = registry
    seller = await create_seller(client, "Реестр Стирание", "wb-registry-key-5")
    seller_id = uuid.UUID(seller["id"])
    await client.post(f"{API}/automations/wb-reviews/sellers", json={"seller_id": str(seller_id)})
    async with application.database.session() as session:
        session.add(DailyReviewCountModel(seller_id=seller_id, article="123", date=date(2026, 8, 18), count_rating_5=3))
        await session.commit()

    assert (await client.delete(f"{API}/wb/sellers/{seller_id}", params={"purge": True})).status_code == 204

    assert (await client.get(f"{API}/wb/sellers", params={"include_archived": True})).json() == []
    async with application.database.session() as session:
        assert (
            await session.scalar(select(DailyReviewCountModel).where(DailyReviewCountModel.seller_id == seller_id))
            is None
        )
        assert await session.scalar(select(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id)) is None


async def test_a_new_seller_can_be_created_straight_into_an_automation(registry) -> None:
    _, client = registry

    created = await client.post(
        f"{API}/automations/wb-reviews/sellers", json={"name": "Сразу в автоматизацию", "api_key": "wb-registry-key-6"}
    )

    assert created.status_code == 201
    assert created.json()["automations"] == ["wb-reviews"]
    assert len((await client.get(f"{API}/automations/wb-reviews/sellers")).json()) == 1


async def test_an_unknown_automation_is_not_silently_accepted(registry) -> None:
    _, client = registry
    seller = await create_seller(client, "Реестр 404", "wb-registry-key-7")

    attached = await client.post(f"{API}/automations/does-not-exist/sellers", json={"seller_id": seller["id"]})

    assert attached.status_code == 404
    assert (await client.get(f"{API}/automations/does-not-exist/sellers")).status_code == 404
