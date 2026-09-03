from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from backend.app.application import Application
from backend.modules.wb_fbs_distribution.infrastructure.postgres import StockPoolModel, StockSnapshotModel
from backend.shared.settings import load_settings

API = "/api/v1/onec"
TOKEN = "test-onec-token-0000000000"


# Свежий момент формирования: снимок со вчерашней датой честно считался бы
# устаревшим, а тест проверяет приём, а не устаревание.
def fresh() -> str:
    return datetime.now(UTC).isoformat()


SNAPSHOT = {
    "generated_at": "2026-08-27T09:00:00+03:00",
    "lines": [
        {"item_id": "НФ-000148", "barcode": "2053377116476", "name": "Болгарка", "characteristic": "", "quantity": 196},
        {
            "item_id": "НФ-000151",
            "barcode": "2054994609839",
            "name": "Шуруповерт",
            "characteristic": "",
            "quantity": 89,
        },
    ],
}


def application(token: str | None) -> Application:
    settings = load_settings("backend/shared/settings/config.test.yaml")
    if token is not None:
        settings = replace(settings, fbs_distribution=replace(settings.fbs_distribution, onec_token=token))
    return Application(settings)


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async for item in _client(application(TOKEN)):
        yield item


@pytest_asyncio.fixture
async def unconfigured() -> AsyncIterator[AsyncClient]:
    async for item in _client(application(None)):
        yield item


async def _client(app_instance: Application) -> AsyncIterator[AsyncClient]:
    app = app_instance.get_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client,
    ):
        try:
            yield test_client
        finally:
            async with app_instance.database.session() as session:
                await session.execute(delete(StockPoolModel))
                await session.execute(delete(StockSnapshotModel))
                await session.commit()


def bearer(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_without_a_configured_token_the_exchange_is_off(unconfigured) -> None:
    """Пустой токен — выключенный обмен, а не эндпоинт, открытый всем."""
    assert (await unconfigured.get(f"{API}/ping")).status_code == 503
    assert (await unconfigured.post(f"{API}/stocks", json=SNAPSHOT)).status_code == 503


async def test_a_wrong_or_missing_token_is_refused(client) -> None:
    assert (await client.get(f"{API}/ping")).status_code == 401
    assert (await client.post(f"{API}/stocks", json=SNAPSHOT, headers=bearer("wrong"))).status_code == 401
    # Сотрудничий JWT здесь тоже не пропуск: у обмена свой токен.
    assert (await client.post(f"{API}/stocks", json=SNAPSHOT, headers=bearer("not-the-token"))).status_code == 401


async def test_ping_lets_the_1c_side_verify_the_channel_first(client) -> None:
    response = await client.get(f"{API}/ping", headers=bearer())

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_a_snapshot_becomes_pools_and_is_signed_by_the_exchange(client) -> None:
    response = await client.post(f"{API}/stocks", json={**SNAPSHOT, "generated_at": fresh()}, headers=bearer())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "1c"
    assert (body["pools"], body["on_hand_total"]) == (2, 285)
    assert body["stale"] is False

    pools = (await client.get(f"{API.replace('/onec', '/wb/fbs')}/stock/pools")).status_code
    # Экраны оператора живут под сотрудничьим JWT; без него 401 — обмен и
    # интерфейс не делят аутентификацию.
    assert pools == 401


async def test_resending_the_same_snapshot_doubles_nothing(client) -> None:
    await client.post(f"{API}/stocks", json=SNAPSHOT, headers=bearer())
    response = await client.post(f"{API}/stocks", json=SNAPSHOT, headers=bearer())

    assert response.status_code == 200
    assert (response.json()["pools"], response.json()["on_hand_total"]) == (2, 285)


async def test_a_broken_snapshot_is_refused_whole_with_the_reason(client) -> None:
    broken = {
        "generated_at": "2026-08-27T09:00:00+03:00",
        "lines": [{"item_id": "A1", "barcode": "2000", "quantity": -5}],
    }

    response = await client.post(f"{API}/stocks", json=broken, headers=bearer())

    assert response.status_code == 422
    assert "Отрицательный" in response.json()["detail"]


async def test_a_line_with_no_identity_names_its_number(client) -> None:
    nameless = {"generated_at": "2026-08-27T09:00:00+03:00", "lines": [{"quantity": 5}]}

    response = await client.post(f"{API}/stocks", json=nameless, headers=bearer())

    assert response.status_code == 422
    assert "Строка 1" in response.json()["detail"]


async def test_a_barcode_alone_identifies_the_line(client) -> None:
    """Есть ли у 1С стабильные GUID — ещё не подтверждено; выгрузка из одних
    баркодов не должна быть мертворождённой."""
    barcode_only = {
        "generated_at": "2026-08-27T09:00:00+03:00",
        "lines": [{"barcode": "2053377116476", "quantity": 7}],
    }

    response = await client.post(f"{API}/stocks", json=barcode_only, headers=bearer())

    assert response.status_code == 200
    assert response.json()["pools"] == 1
