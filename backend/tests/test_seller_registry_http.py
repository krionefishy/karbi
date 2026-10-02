import uuid
from collections.abc import AsyncIterator
from datetime import date

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from backend.app.application import Application
from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel, SellerModel
from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.modules.wb_reviews.infrastructure.postgres.models import DailyReviewCountModel, TrackedSellerModel
from backend.shared.settings import load_settings

API = "/api/v1"


@pytest_asyncio.fixture
async def registry(monkeypatch) -> AsyncIterator[tuple[Application, AsyncClient]]:
    # Ключи живут на шлюзе wb-egress; здесь его управляющие вызовы всегда рады.
    async def put_seller(self, *, seller_id: str, name: str, api_key: str, event_version: int) -> dict:
        return {
            "seller_id": seller_id,
            "name": name,
            "status": "verified",
            "egress_ip": "185.0.0.1",
            "event_version": event_version,
            "verify_error": "",
        }

    async def rename_seller(self, *, seller_id: str, name: str, event_version: int) -> dict:
        return {"seller_id": seller_id, "name": name, "event_version": event_version}

    async def disable_seller(self, *, seller_id: str, event_version: int) -> dict:
        return {"seller_id": seller_id, "status": "disabled", "event_version": event_version}

    async def put_mpstats_credentials(self, *, seller_id: str, name: str, token: str, event_version: int) -> dict:
        return {
            "seller_id": seller_id,
            "name": name,
            "status": "verified",
            "egress_ip": "185.0.0.1",
            "event_version": event_version,
            "marketplaces": {
                "wb": {"status": "verified", "verify_error": ""},
                "mpstats": {"status": "verified", "verify_error": ""},
            },
        }

    monkeypatch.setattr(EgressGateway, "put_seller", put_seller)
    monkeypatch.setattr(EgressGateway, "put_mpstats_credentials", put_mpstats_credentials)
    monkeypatch.setattr(EgressGateway, "rename_seller", rename_seller)
    monkeypatch.setattr(EgressGateway, "disable_seller", disable_seller)
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
    _, client = registry
    seller = await create_seller(client, "Реестр Архив", "wb-registry-key-3")
    seller_id = uuid.UUID(seller["id"])
    await client.post(f"{API}/automations/wb-reviews/sellers", json={"seller_id": str(seller_id)})

    assert (await client.delete(f"{API}/wb/sellers/{seller_id}")).status_code == 204

    assert (await client.get(f"{API}/wb/sellers")).json() == []
    archived = (await client.get(f"{API}/wb/sellers", params={"include_archived": True})).json()
    assert archived[0]["archived_at"] is not None
    # Подключение остаётся за селлером: восстановят — вернётся в автоматизацию сам.
    assert archived[0]["automations"] == ["wb-reviews"]
    assert (await client.get(f"{API}/automations/wb-reviews/sellers")).json() == []
    # The archived seller may not be collected for, and the same key can be
    # handed to a new one: it never lived in this base, and the gateway was
    # told to disable the archived seller.
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


async def test_an_mpstats_token_is_connected_to_an_existing_seller(registry) -> None:
    """Токен уезжает на шлюз; в реестре остаётся только исход доставки, в ответе токена нет."""
    application, client = registry
    seller = await create_seller(client, "Реестр MPStats", "wb-registry-key-9")
    assert seller["mpstats_egress_status"] == "undelivered"

    connected = await client.put(f"{API}/wb/sellers/{seller['id']}/mpstats", json={"token": "aaaa1111.bbbb2222"})

    assert connected.status_code == 200, connected.text
    assert connected.json()["mpstats_egress_status"] == "verified"
    assert connected.json()["egress_status"] == "verified"
    assert "aaaa1111" not in connected.text
    async with application.database.session() as session:
        stored = await session.get(SellerModel, uuid.UUID(seller["id"]))
        assert stored is not None and stored.mpstats_egress_status == "verified"


async def test_a_token_pasted_in_pieces_never_reaches_the_gateway(registry) -> None:
    _, client = registry
    seller = await create_seller(client, "Реестр MPStats 2", "wb-registry-key-10")

    refused = await client.put(f"{API}/wb/sellers/{seller['id']}/mpstats", json={"token": "aaaa1111 bbbb2222"})

    assert refused.status_code == 422
    # Отвергнутое значение не должно вернуться в тексте ошибки.
    assert "aaaa1111" not in refused.text


async def test_a_tax_rate_takes_effect_on_its_date_and_keeps_the_previous_one(registry) -> None:
    """Ставка — версиями: будущая не действует сегодня, а первая покрывает и прошлое."""
    _, client = registry
    seller = await create_seller(client, "Реестр Налог", "wb-registry-key-tax")
    assert (seller["tax_rate"], seller["tax_rate_from"]) == (None, None)
    path = f"{API}/wb/sellers/{seller['id']}/tax-rate"

    first = await client.put(path, json={"rate": "8", "effective_from": "2026-01-01"})
    planned = await client.put(path, json={"rate": "10.5", "effective_from": "2999-01-01"})

    assert first.status_code == 200, first.text
    assert (first.json()["tax_rate"], first.json()["tax_rate_from"]) == (8.0, "2026-01-01")
    # Ставка с будущей даты записана, но сегодня действует прежняя.
    assert (planned.json()["tax_rate"], planned.json()["tax_rate_from"]) == (8.0, "2026-01-01")
    corrected = await client.put(path, json={"rate": "12", "effective_from": "2026-01-01"})
    assert corrected.json()["tax_rate"] == 12.0
    [listed] = [item for item in (await client.get(f"{API}/wb/sellers")).json() if item["id"] == seller["id"]]
    assert listed["tax_rate"] == 12.0
    assert (await client.put(path, json={"rate": "101"})).status_code == 422
    assert (await client.put(f"{API}/wb/sellers/{uuid.uuid4()}/tax-rate", json={"rate": "8"})).status_code == 404
