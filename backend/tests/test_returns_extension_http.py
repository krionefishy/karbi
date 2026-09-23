"""Расширение через приложение целиком: DI, роуты и токен, а не сервис по отдельности."""

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from backend.app.application import Application
from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel, SellerModel
from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.modules.wb_returns.infrastructure.postgres import ReturnsRepository
from backend.shared.settings import load_settings

API = "/api/v1"


@pytest_asyncio.fixture
async def workspace(monkeypatch) -> AsyncIterator[tuple[Application, AsyncClient]]:
    async def put_seller(self, *, seller_id: str, name: str, api_key: str, event_version: int) -> dict:
        return {
            "seller_id": seller_id,
            "name": name,
            "status": "verified",
            "egress_ip": "185.0.0.1",
            "event_version": event_version,
            "verify_error": "",
        }

    monkeypatch.setattr(EgressGateway, "put_seller", put_seller)
    application = Application(load_settings("backend/shared/settings/config.test.yaml"))
    app = application.get_app()
    async with app.router.lifespan_context(app):
        token = application.token_service.issue_access(uuid.uuid4())
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
        ) as client:
            try:
                yield application, client
            finally:
                async with application.database.session() as session:
                    for seller_id in await ReturnsRepository(session).tracked_seller_ids():
                        await ReturnsRepository(session).purge_seller(seller_id)
                    await session.execute(delete(OutboxEventModel))
                    await session.execute(delete(SellerModel))
                    await session.commit()


async def test_extension_pairs_reports_and_serves_qr_through_the_api(workspace) -> None:
    _, client = workspace
    created = await client.post(
        f"{API}/automations/wb-returns/sellers", json={"name": "Расширение Ромашка", "api_key": "wb-ext-key-1"}
    )
    assert created.status_code == 201, created.text
    seller_id = created.json()["id"]

    state = await client.get(f"{API}/wb/returns/sellers/{seller_id}/extension")
    assert state.status_code == 200, state.text
    assert state.json()["installs"] == [] and state.json()["download_url"].endswith(
        "/extension/marketplace-auto-returns.zip"
    )

    pairing = await client.post(f"{API}/wb/returns/sellers/{seller_id}/extension/pairing-code")
    assert pairing.status_code == 200, pairing.text
    code = pairing.json()["code"]

    # Пара и данные идут без сотрудничьего JWT — у расширения свой токен.
    anonymous = {"Authorization": ""}
    refused = await client.post(
        f"{API}/wb/returns/extension/pair",
        json={"code": "000000", "install_id": "chrome-deadbeef", "browser": "Chrome"},
        headers=anonymous,
    )
    assert refused.status_code == 404
    paired = await client.post(
        f"{API}/wb/returns/extension/pair",
        json={"code": code, "install_id": "chrome-deadbeef", "browser": "Chrome 130"},
        headers=anonymous,
    )
    assert paired.status_code == 200, paired.text
    token = paired.json()["token"]
    assert paired.json()["seller_name"] == "Расширение Ромашка"
    as_extension = {"Authorization": f"Bearer {token}"}

    bad = await client.post(
        f"{API}/wb/returns/extension/heartbeat", json={"state": "ok"}, headers={"Authorization": "Bearer mar_nope"}
    )
    assert bad.status_code == 401

    beat = await client.post(f"{API}/wb/returns/extension/heartbeat", json={"state": "ok"}, headers=as_extension)
    assert beat.status_code == 200, beat.text
    assert beat.json() == {"seller_name": "Расширение Ромашка", "has_code_today": False, "refresh_code": False}

    from datetime import UTC, datetime
    from zoneinfo import ZoneInfo

    today = datetime.now(UTC).astimezone(ZoneInfo("Europe/Moscow")).date().isoformat()
    sent = await client.post(
        f"{API}/wb/returns/extension/codes",
        json={"codes": [{"date": today, "code": "412", "qr": "WB|412|test"}]},
        headers=as_extension,
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["accepted"] == 1
    deliveries = await client.post(
        f"{API}/wb/returns/extension/deliveries",
        json={"items": [{"rid": "1", "status": "ready"}]},
        headers=as_extension,
    )
    assert deliveries.status_code == 200 and deliveries.json()["accepted"] == 1

    state = await client.get(f"{API}/wb/returns/sellers/{seller_id}/extension")
    body = state.json()
    assert body["has_code_today"] and len(body["installs"]) == 1
    assert body["installs"][0]["browser"] == "Chrome 130" and body["installs"][0]["deliveries_count"] == 1

    # Картинка QR по подписанной ссылке, без входа.
    from backend.modules.wb_returns.application import qr as qr_tools

    signature = qr_tools.signature(
        "test-only-secret-at-least-32-characters", uuid.UUID(seller_id), datetime.fromisoformat(today).date()
    )
    image = await client.get(f"{API}/wb/returns/qr/{seller_id}/{today}/{signature}.png", headers=anonymous)
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    forged = await client.get(f"{API}/wb/returns/qr/{seller_id}/{today}/{'0' * 40}.png", headers=anonymous)
    assert forged.status_code == 404

    revoked = await client.delete(
        f"{API}/wb/returns/sellers/{seller_id}/extension/installs/{body['installs'][0]['id']}"
    )
    assert revoked.status_code == 204
    gone = await client.post(f"{API}/wb/returns/extension/heartbeat", json={"state": "ok"}, headers=as_extension)
    assert gone.status_code == 401
