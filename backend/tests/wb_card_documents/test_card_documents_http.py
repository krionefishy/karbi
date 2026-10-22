import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, update

from backend.app.factory import Application
from backend.modules.notifications.infrastructure.postgres.models import BotModel
from backend.modules.wb_card_documents.infrastructure.postgres import CardDocumentsRepository
from backend.modules.wb_core.infrastructure.postgres.models import SellerModel
from backend.shared.settings import load_settings

API = "/api/v1/wb/card-documents"
AUTOMATION = "/api/v1/automations/wb-card-documents/sellers"
SETTINGS = load_settings("backend/shared/settings/config.test.yaml")


@pytest_asyncio.fixture
async def application() -> AsyncIterator[Application]:
    application = Application(SETTINGS)
    app = application.get_app()
    async with app.router.lifespan_context(app):
        yield application


@pytest_asyncio.fixture
async def seller(application: Application) -> AsyncIterator[uuid.UUID]:
    async with application.database.session() as session:
        model = SellerModel(name="ИП Документы HTTP", catalog_sync_status="success", egress_status="verified")
        session.add(model)
        await session.flush()
        seller_id = model.id
        await session.commit()
    try:
        yield seller_id
    finally:
        async with application.database.session() as session:
            await CardDocumentsRepository(session).purge_seller(seller_id)
            await session.execute(delete(SellerModel).where(SellerModel.id == seller_id))
            await session.commit()


@pytest_asyncio.fixture
async def client(application: Application) -> AsyncIterator[AsyncClient]:
    token = application.token_service.issue_access(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=application.get_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as test_client:
        yield test_client


async def test_a_seller_that_is_not_connected_has_no_page(client: AsyncClient, seller: uuid.UUID) -> None:
    assert (await client.get(f"{API}/sellers/{seller}")).status_code == 404
    assert (await client.get(f"{API}/sellers/{seller}/refresh")).status_code == 404
    assert (await client.post(f"{API}/sellers/{seller}/refresh")).status_code == 404
    assert (await client.post(f"{API}/sellers/{seller}/invite-link")).status_code == 404
    assert (await client.get(f"{API}/sellers/{uuid.uuid4()}")).status_code == 404


async def test_an_archived_seller_has_no_page_even_when_connected(
    application: Application, client: AsyncClient, seller: uuid.UUID
) -> None:
    assert (await client.post(AUTOMATION, json={"seller_id": str(seller)})).status_code == 201
    async with application.database.session() as session:
        await session.execute(update(SellerModel).where(SellerModel.id == seller).values(archived_at=datetime.now(UTC)))
        await session.commit()

    assert (await client.get(f"{API}/sellers/{seller}")).status_code == 404


async def test_a_connected_seller_starts_empty_and_can_press_refresh(client: AsyncClient, seller: uuid.UUID) -> None:
    assert (await client.post(AUTOMATION, json={"seller_id": str(seller)})).status_code == 201
    assert str(seller) in {item["id"] for item in (await client.get(AUTOMATION)).json()}

    body = (await client.get(f"{API}/sellers/{seller}")).json()
    assert body == {
        "collected_at": None,
        "last_error": None,
        "stale": True,
        "summary": {"total": 0, "ok": 0, "rejected": 0, "missing": 0, "expiring": 0, "mismatch": 0},
        "items": [],
    }
    assert (await client.get(f"{API}/sellers/{seller}/refresh")).json() is None

    pressed = await client.post(f"{API}/sellers/{seller}/refresh")
    assert pressed.status_code == 202
    assert (pressed.json()["status"], pressed.json()["in_progress"]) == ("queued", True)
    assert (await client.get(f"{API}/sellers/{seller}/refresh")).json()["requested_at"] == pressed.json()[
        "requested_at"
    ]


async def test_an_unknown_state_filter_is_rejected(client: AsyncClient, seller: uuid.UUID) -> None:
    await client.post(AUTOMATION, json={"seller_id": str(seller)})

    assert (await client.get(f"{API}/sellers/{seller}", params={"state": "broken"})).status_code == 422
    assert (await client.get(f"{API}/sellers/{seller}", params={"state": "rejected"})).status_code == 200


async def test_the_invite_link_needs_a_registered_bot(
    application: Application, client: AsyncClient, seller: uuid.UUID
) -> None:
    await client.post(AUTOMATION, json={"seller_id": str(seller)})
    code = SETTINGS.card_documents.notification_bot
    async with application.database.session() as session:
        await session.execute(delete(BotModel).where(BotModel.code == code))
        await session.commit()

    missing = await client.post(f"{API}/sellers/{seller}/invite-link")
    assert missing.status_code == 409

    async with application.database.session() as session:
        session.add(BotModel(code=code, title="Документы", invite_link_template="https://t.me/docs?start={token}"))
        await session.commit()
    try:
        created = await client.post(f"{API}/sellers/{seller}/invite-link")
        assert created.status_code == 200
        assert created.json()["url"].startswith("https://t.me/docs?start=")
    finally:
        async with application.database.session() as session:
            await session.execute(delete(BotModel).where(BotModel.code == code))
            await session.commit()


async def test_the_catalog_lists_the_automation(client: AsyncClient) -> None:
    catalog = (await client.get("/api/v1/automations")).json()

    [entry] = [item for item in catalog if item["id"] == "wb-card-documents"]
    assert entry["title"] == "Документы карточек WB"
    assert entry["kind"] == "automation"
    assert entry["next_run_at"] is not None
