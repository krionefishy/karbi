import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from backend.app.application import Application
from backend.modules.wb_core.infrastructure.postgres.models import SellerModel
from backend.modules.wb_support_bot.infrastructure.postgres import SupportBotRepository
from backend.shared.settings import load_settings

API = "/api/v1/wb/support-bot"
AUTOMATION = "/api/v1/automations/wb-support-bot/sellers"


@pytest_asyncio.fixture
async def application() -> AsyncIterator[Application]:
    application = Application(load_settings("backend/shared/settings/config.test.yaml"))
    app = application.get_app()
    async with app.router.lifespan_context(app):
        yield application


@pytest_asyncio.fixture
async def seller(application: Application) -> AsyncIterator[uuid.UUID]:
    async with application.database.session() as session:
        model = SellerModel(name="ИП Бот HTTP", catalog_sync_status="success", egress_status="verified")
        session.add(model)
        await session.flush()
        seller_id = model.id
        await session.commit()
    try:
        yield seller_id
    finally:
        async with application.database.session() as session:
            await SupportBotRepository(session).purge(seller_id)
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
    assert (await client.get(f"{API}/sellers/{seller}/dialogs")).status_code == 404


async def test_a_connected_seller_starts_in_dry_run_without_texts(client: AsyncClient, seller: uuid.UUID) -> None:
    assert (await client.post(AUTOMATION, json={"seller_id": str(seller)})).status_code == 201

    body = (await client.get(f"{API}/sellers/{seller}")).json()

    assert body["mode"] == "dry_run" and body["templates"] == [] and body["globally_paused"] is False
    assert body["counts"] == {}


async def test_templates_are_added_switched_and_deleted(client: AsyncClient, seller: uuid.UUID) -> None:
    await client.post(AUTOMATION, json={"seller_id": str(seller)})

    created = await client.post(f"{API}/sellers/{seller}/templates", json={"text": "  Здравствуйте! Как дела?  "})
    assert created.status_code == 201 and created.json()["text"] == "Здравствуйте! Как дела?"
    template_id = created.json()["id"]

    off = await client.put(f"{API}/sellers/{seller}/templates/{template_id}/active", json={"active": False})
    assert off.json()["active"] is False

    assert (await client.post(f"{API}/sellers/{seller}/templates", json={"text": "   "})).status_code == 422
    assert (await client.delete(f"{API}/sellers/{seller}/templates/{template_id}")).status_code == 204
    assert (await client.delete(f"{API}/sellers/{seller}/templates/{template_id}")).status_code == 422


async def test_the_mode_can_be_switched_and_garbage_is_refused(client: AsyncClient, seller: uuid.UUID) -> None:
    await client.post(AUTOMATION, json={"seller_id": str(seller)})

    assert (await client.put(f"{API}/sellers/{seller}/mode", json={"mode": "live"})).status_code == 204
    assert (await client.get(f"{API}/sellers/{seller}")).json()["mode"] == "live"
    assert (await client.put(f"{API}/sellers/{seller}/mode", json={"mode": "wild"})).status_code == 422


async def test_an_unknown_dialog_filter_is_refused(client: AsyncClient, seller: uuid.UUID) -> None:
    await client.post(AUTOMATION, json={"seller_id": str(seller)})

    assert (await client.get(f"{API}/sellers/{seller}/dialogs", params={"state": "nonsense"})).status_code == 422
    assert (await client.get(f"{API}/sellers/{seller}/dialogs", params={"flag": "nonsense"})).status_code == 422
    assert (await client.get(f"{API}/sellers/{seller}/dialogs")).json()["total"] == 0


async def test_the_automation_shows_up_in_the_catalog(client: AsyncClient, seller: uuid.UUID) -> None:
    await client.post(AUTOMATION, json={"seller_id": str(seller)})

    catalog = (await client.get("/api/v1/automations")).json()

    assert "wb-support-bot" in {item["id"] for item in catalog}
