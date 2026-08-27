import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from backend.app.application import Application
from backend.modules.wb_fbs_distribution.infrastructure.postgres import StockPoolModel, StockSnapshotModel
from backend.shared.settings import load_settings

API = "/api/v1/wb/fbs"


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


async def test_without_an_exchange_the_state_says_so(client) -> None:
    body = (await client.get(f"{API}/stock")).json()

    assert body["source"] == "disconnected"
    assert body["snapshot_id"] is None
    assert body["stale"] is True
    assert (body["pools"], body["on_hand_total"]) == (0, 0)


async def test_there_is_no_way_to_hand_the_module_a_snapshot_by_hand(client) -> None:
    """Загрузка файлом убрана: остаток приходит только из обмена с 1С."""
    assert (await client.post(f"{API}/stock", content=b"item_id;quantity\nA1;5\n")).status_code == 405


async def test_the_journal_and_the_pools_answer_empty_until_1c_is_connected(client) -> None:
    assert (await client.get(f"{API}/stock/history")).json() == []
    assert (await client.get(f"{API}/stock/pools")).json() == []
