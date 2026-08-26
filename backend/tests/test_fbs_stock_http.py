import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from backend.app.application import Application
from backend.modules.wb_fbs_distribution.infrastructure.postgres import StockPoolModel, StockSnapshotModel
from backend.shared.settings import load_settings

API = "/api/v1/wb/fbs"
CSV = (
    "item_id;barcode;name;characteristic;quantity\n"
    "НФ-001;2000000000017;Футболка синяя;M;150\n"
    "НФ-001;2000000000024;Футболка синяя;L;8\n"
)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    application = Application(load_settings("backend/shared/settings/config.test.yaml"))
    app = application.get_app()
    async with app.router.lifespan_context(app):
        token = application.token_service.issue_access(uuid.uuid4())
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as test_client:
            try:
                yield test_client
            finally:
                async with application.database.session() as session:
                    await session.execute(delete(StockPoolModel))
                    await session.execute(delete(StockSnapshotModel))
                    await session.commit()


async def test_a_snapshot_is_posted_as_a_plain_body(client) -> None:
    """No multipart wrapper: the future 1C adapter will POST the very same body."""
    response = await client.post(
        f"{API}/stock", content=CSV.encode("utf-8"), headers={"Content-Type": "text/plain; charset=utf-8"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["pools"], body["on_hand_total"], body["available_total"]) == (2, 158, 138)
    assert body["source"] == "manual"


async def test_the_pools_show_up_with_what_is_left_after_the_reserve(client) -> None:
    await client.post(f"{API}/stock", content=CSV.encode("utf-8"))

    pools = (await client.get(f"{API}/stock/pools", params={"search": "0000017"})).json()

    assert len(pools) == 1
    assert (pools[0]["on_hand"], pools[0]["available"]) == (150, 130)


async def test_a_file_that_cannot_be_read_is_refused_with_a_reason(client) -> None:
    response = await client.post(f"{API}/stock", content=b"item_id;quantity\nA1;\n")

    assert response.status_code == 422
    assert "нет остатка" in response.json()["detail"]


async def test_a_rejected_upload_lands_in_the_history(client) -> None:
    await client.post(f"{API}/stock", content=b"item_id;barcode;quantity\nA1;2000;-5\n")

    history = (await client.get(f"{API}/stock/history")).json()

    assert [record["status"] for record in history] == ["rejected"]
    assert "Отрицательный" in history[0]["error"]


async def test_without_a_snapshot_the_state_admits_there_is_no_exchange(client) -> None:
    body = (await client.get(f"{API}/stock")).json()

    assert body["source"] == "disconnected"
    assert body["snapshot_id"] is None
    assert body["stale"] is True
