import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from backend.app.factory import Application
from backend.modules.wb_core.infrastructure.postgres.models import ArticleModel, SellerModel
from backend.modules.wb_support_bot.infrastructure.postgres import (
    DialogModel,
    SupportBotRepository,
    VerdictModel,
)
from backend.shared.settings import load_settings
from backend.tests.operators import operator_token

API = "/api/v1/wb/support-bot"
AUTOMATION = "/api/v1/automations/wb-support-bot/sellers"
PROMPT = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
A, B, C = 111, 222, 333


@pytest_asyncio.fixture
async def application() -> AsyncIterator[Application]:
    base = load_settings("backend/shared/settings/config.test.yaml")
    settings = replace(base, support_bot=replace(base.support_bot, classifier_mode="enforce"))
    application = Application(settings)
    app = application.get_app()
    async with app.router.lifespan_context(app):
        yield application


@pytest_asyncio.fixture
async def seller(application: Application) -> AsyncIterator[uuid.UUID]:
    async with application.database.session() as session:
        model = SellerModel(name="ИП Тумблеры", catalog_sync_status="success", egress_status="verified")
        session.add(model)
        await session.flush()
        seller_id = model.id
        await session.commit()
    try:
        yield seller_id
    finally:
        async with application.database.session() as session:
            await SupportBotRepository(session).purge_seller(seller_id)
            await session.execute(delete(ArticleModel).where(ArticleModel.seller_id == seller_id))
            await session.execute(delete(SellerModel).where(SellerModel.id == seller_id))
            await session.commit()


@pytest_asyncio.fixture
async def client(application: Application) -> AsyncIterator[AsyncClient]:
    token = await operator_token(application)
    async with AsyncClient(
        transport=ASGITransport(app=application.get_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as test_client:
        yield test_client


def dialog(
    seller_id: uuid.UUID, chat: str, nm_id: int | None, state: str = "in_campaign", **extra: object
) -> DialogModel:
    return DialogModel(
        seller_id=seller_id,
        chat_id=chat,
        prompt_at=PROMPT,
        state=state,
        nm_id=nm_id,
        tail_event_id=f"e-{chat}",
        tail_fingerprint=(chat.ljust(40, "x"))[:40],
        **extra,
    )


def verdict(seller_id: uuid.UUID, version: str, chat: str, value: str, reason: str = "") -> VerdictModel:
    return VerdictModel(
        id=uuid.uuid4(),
        seller_id=seller_id,
        chat_id=chat,
        tail_event_id=f"e-{chat}",
        tail_fingerprint=(chat.ljust(40, "x"))[:40],
        prompt_version=version,
        status="done",
        verdict=value,
        reason=reason,
    )


@pytest_asyncio.fixture
async def data(application: Application, client: AsyncClient, seller: uuid.UUID) -> uuid.UUID:
    """Кабинет с тремя товарами и группой без артикула; третий товар выключен."""
    await client.post(AUTOMATION, json={"seller_id": str(seller)})
    version = application.settings.support_bot.classifier_prompt_version
    async with application.database.session() as session:
        session.add_all(
            [
                ArticleModel(
                    seller_id=seller, article=str(A), name="Вакуумный упаковщик", vendor_code="vac-1", state="active"
                ),
                ArticleModel(
                    seller_id=seller, article=str(C), name="Старая лампа", vendor_code="lamp", state="archived"
                ),
                dialog(seller, "1:a-open", A),
                dialog(seller, "1:a-closing", A),
                dialog(seller, "1:a-wait", A, state="awaiting_buyer"),
                dialog(seller, "1:a-done", A, state="exhausted"),
                dialog(seller, "1:b-open", B),
                dialog(seller, "1:c-open", C),
                dialog(seller, "1:n-open", None),
                verdict(seller, version, "1:a-open", "open", "просит выбрать вариант"),
                verdict(seller, version, "1:a-closing", "closing", "заявка одобрена"),
                verdict(seller, version, "1:b-open", "open"),
                verdict(seller, version, "1:c-open", "open"),
                verdict(seller, version, "1:n-open", "open"),
            ]
        )
        await SupportBotRepository(session).set_product_enabled(seller, C, False)
        await session.commit()
    return seller


async def groups(client: AsyncClient, seller: uuid.UUID, scope: str = "campaign") -> list[dict]:
    response = await client.get(f"{API}/sellers/{seller}/products", params={"scope": scope})
    assert response.status_code == 200
    return response.json()["products"]


async def rows(client: AsyncClient, seller: uuid.UUID, key: int, scope: str = "campaign") -> dict[str, dict]:
    response = await client.get(f"{API}/sellers/{seller}/products/{key}/dialogs", params={"scope": scope})
    assert response.status_code == 200
    return {item["chat_id"]: item for item in response.json()["dialogs"]}


def switch_body(chat: str, on: bool | None) -> dict:
    return {"chat_id": chat, "prompt_at": PROMPT.isoformat(), "on": on}


async def test_products_come_enabled_first_by_activity_and_disabled_last(client: AsyncClient, data: uuid.UUID) -> None:
    result = await groups(client, data)

    assert [group["key"] for group in result] == [A, 0, B, C], "включённые по числу «пишем», выключенный в конце"
    first = result[0]
    assert (first["name"], first["vendor_code"], first["enabled"]) == ("Вакуумный упаковщик", "vac-1", True)
    assert (first["total"], first["writing"], first["waiting"]) == (3, 1, 2)
    assert result[1]["name"] == "Без артикула" and result[1]["nm_id"] is None
    assert result[2]["name"] == "Артикул 222"
    last = result[3]
    assert (last["enabled"], last["total"], last["writing"], last["removed"]) == (False, 1, 0, True)


async def test_the_all_scope_also_counts_dialogs_without_a_mailing(client: AsyncClient, data: uuid.UUID) -> None:
    default = {group["key"]: group for group in await groups(client, data)}
    everything = {group["key"]: group for group in await groups(client, data, "all")}

    assert default[A]["total"] == 3 and everything[A]["total"] == 4
    assert (await client.get(f"{API}/sellers/{data}/products", params={"scope": "weird"})).status_code == 422


async def test_every_dialog_shows_what_the_model_decided_and_why(client: AsyncClient, data: uuid.UUID) -> None:
    shown = await rows(client, data, A)

    assert list(shown)[0] == "1:a-open", "те, по кому пишем, выше"
    on, off, wait = shown["1:a-open"], shown["1:a-closing"], shown["1:a-wait"]
    assert on["mailing"] == {
        "on": True,
        "source": "model",
        "reason": "verdict_open",
        "locked": False,
        "manual_until": None,
    }
    assert on["verdict"] == "open" and on["verdict_reason"] == "просит выбрать вариант"
    assert (off["mailing"]["on"], off["mailing"]["reason"]) == (False, "verdict_closing")
    assert (wait["mailing"]["on"], wait["mailing"]["reason"]) == (False, "verdict_pending")
    assert "1:a-done" not in shown and "1:a-done" in await rows(client, data, A, "all")
    done = (await rows(client, data, A, "all"))["1:a-done"]["mailing"]
    assert (done["on"], done["reason"], done["locked"]) == (False, "state", True)


async def test_a_disabled_product_locks_all_its_dialogs(client: AsyncClient, data: uuid.UUID) -> None:
    mailing = (await rows(client, data, C))["1:c-open"]["mailing"]

    assert (mailing["on"], mailing["source"], mailing["reason"], mailing["locked"]) == (
        False,
        "product",
        "product_off",
        True,
    )


async def test_a_product_can_be_switched_off_and_back_on(client: AsyncClient, data: uuid.UUID) -> None:
    off = await client.put(f"{API}/sellers/{data}/products/{B}/switch", json={"enabled": False})
    assert off.status_code == 204
    after = await groups(client, data)
    assert [group["key"] for group in after][-2:] == [B, C] or [group["key"] for group in after][-2:] == [C, B]
    assert next(group for group in after if group["key"] == B)["enabled"] is False
    assert next(group for group in after if group["key"] == B)["writing"] == 0
    assert (await rows(client, data, B))["1:b-open"]["mailing"]["reason"] == "product_off"

    on = await client.put(f"{API}/sellers/{data}/products/{B}/switch", json={"enabled": True})
    assert on.status_code == 204
    back = await groups(client, data)
    assert [group["key"] for group in back] == [A, 0, B, C]
    assert (await rows(client, data, B))["1:b-open"]["mailing"]["on"] is True


async def test_a_product_switch_is_refused_for_an_unknown_seller_or_a_bad_key(
    client: AsyncClient, data: uuid.UUID
) -> None:
    assert (
        await client.put(f"{API}/sellers/{uuid.uuid4()}/products/{A}/switch", json={"enabled": False})
    ).status_code == 404
    assert (await client.put(f"{API}/sellers/{data}/products/-5/switch", json={"enabled": False})).status_code == 422


async def test_switching_a_dialog_by_hand_lasts_a_day_and_can_be_dropped(client: AsyncClient, data: uuid.UUID) -> None:
    now = datetime.now(UTC)

    forced = await client.post(f"{API}/sellers/{data}/dialogs/switch", json=switch_body("1:a-closing", True))
    assert forced.status_code == 204
    mailing = (await rows(client, data, A))["1:a-closing"]["mailing"]
    assert (mailing["on"], mailing["source"], mailing["reason"]) == (True, "manual", "manual_on")
    until = datetime.fromisoformat(mailing["manual_until"])
    assert now + timedelta(hours=23, minutes=58) <= until <= now + timedelta(hours=24, minutes=2)

    dropped = await client.post(f"{API}/sellers/{data}/dialogs/switch", json=switch_body("1:a-closing", None))
    assert dropped.status_code == 204
    back = (await rows(client, data, A))["1:a-closing"]["mailing"]
    assert (back["on"], back["source"], back["manual_until"]) == (False, "model", None)


async def test_switching_off_a_dialog_the_model_would_write_to_marks_it_manual(
    client: AsyncClient, data: uuid.UUID
) -> None:
    await client.post(f"{API}/sellers/{data}/dialogs/switch", json=switch_body("1:a-open", False))

    mailing = (await rows(client, data, A))["1:a-open"]["mailing"]

    assert (mailing["on"], mailing["source"], mailing["reason"]) == (False, "manual", "manual_off")
    assert next(group for group in await groups(client, data) if group["key"] == A)["writing"] == 0


async def test_a_switch_that_matches_the_model_leaves_no_manual_mark(client: AsyncClient, data: uuid.UUID) -> None:
    await client.post(f"{API}/sellers/{data}/dialogs/switch", json=switch_body("1:a-open", True))

    mailing = (await rows(client, data, A))["1:a-open"]["mailing"]

    assert (mailing["on"], mailing["source"], mailing["manual_until"]) == (True, "model", None)


async def test_a_locked_dialog_cannot_be_switched(client: AsyncClient, data: uuid.UUID) -> None:
    product_off = await client.post(f"{API}/sellers/{data}/dialogs/switch", json=switch_body("1:c-open", True))
    no_mailing = await client.post(f"{API}/sellers/{data}/dialogs/switch", json=switch_body("1:a-done", True))

    assert product_off.status_code == 422 and "товар выключен" in product_off.json()["detail"]
    assert no_mailing.status_code == 422 and "рассылки нет" in no_mailing.json()["detail"]


async def test_an_unknown_dialog_or_date_is_refused(client: AsyncClient, data: uuid.UUID) -> None:
    assert (
        await client.post(f"{API}/sellers/{data}/dialogs/switch", json=switch_body("1:nope", True))
    ).status_code == 422
    broken = {"chat_id": "1:a-open", "prompt_at": "вчера", "on": True}
    assert (await client.post(f"{API}/sellers/{data}/dialogs/switch", json=broken)).status_code == 422
    assert (
        await client.post(f"{API}/sellers/{uuid.uuid4()}/dialogs/switch", json=switch_body("1:a-open", True))
    ).status_code == 404


async def test_an_excluded_dialog_shows_off_and_the_toggle_brings_it_back(
    application: Application, client: AsyncClient, data: uuid.UUID
) -> None:
    async with application.database.session() as session:
        found = (await session.scalars(select(DialogModel).where(DialogModel.chat_id == "1:b-open"))).one()
        found.excluded = True
        await session.commit()

    shown = (await rows(client, data, B))["1:b-open"]["mailing"]
    assert (shown["on"], shown["source"], shown["reason"], shown["locked"]) == (False, "excluded", "excluded", False)

    await client.post(f"{API}/sellers/{data}/dialogs/switch", json=switch_body("1:b-open", True))

    back = (await rows(client, data, B))["1:b-open"]
    assert back["excluded"] is False
    assert (back["mailing"]["on"], back["mailing"]["source"]) == (True, "model"), "вердикт open включает диалог сам"


async def test_the_old_manual_verdict_endpoint_is_gone(client: AsyncClient, data: uuid.UUID) -> None:
    gone = await client.post(
        f"{API}/sellers/{data}/dialogs/manual-verdict",
        json={"chat_id": "1:a-open", "prompt_at": PROMPT.isoformat(), "value": "allow"},
    )

    assert gone.status_code in (404, 405)
