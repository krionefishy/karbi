import io
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook
from sqlalchemy import delete

from backend.app.application import Application
from backend.modules.wb_core.domain import (
    CHAT_SENDER_CLIENT,
    CHAT_SENDER_SELLER,
    CHAT_SOURCE_API,
    CHAT_SOURCE_PORTAL,
    MIRROR_CHATS,
    ChatEvent,
)
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository
from backend.modules.wb_core.infrastructure.postgres.models import SellerModel
from backend.modules.wb_review_chats.infrastructure.postgres import TrackedSellerModel
from backend.shared.settings import load_settings

API = "/api/v1/wb/review-chats"
AUTOMATION = "/api/v1/automations/wb-review-chats/sellers"
# Неделю назад: окно ответа у всех диалогов давно истекло, а период по умолчанию их ещё видит.
DAY = (datetime.now(UTC) - timedelta(days=7)).replace(hour=9, minute=0, second=0, microsecond=0)


def event(number: int, chat: str, minutes: int, sender: str, source: str, *, prompt: bool = False, text: str = ""):
    return ChatEvent(
        event_id=f"event-{number}",
        chat_id=chat,
        sender=sender,
        source=source,
        added_at=DAY + timedelta(minutes=minutes),
        is_new_chat=prompt,
        review_prompt=prompt,
        nm_id=1304195061 if prompt else None,
        rid=None,
        text=text or None,
        has_attachments=False,
    )


FEED = [
    # Менеджер через сторонний клиент за неделю до рассылки: не момент запуска.
    event(0, "chat-old", -7 * 24 * 60, CHAT_SENDER_SELLER, CHAT_SOURCE_PORTAL, prompt=True, text="Здравствуйте. Вы"),
    event(9, "chat-old", -7 * 24 * 60 + 300, CHAT_SENDER_SELLER, CHAT_SOURCE_API, text="Заявка одобрена"),
    event(1, "chat-a", 0, CHAT_SENDER_SELLER, CHAT_SOURCE_PORTAL, prompt=True, text="Здравствуйте. Вы оставили отзыв"),
    event(2, "chat-a", 1, CHAT_SENDER_SELLER, CHAT_SOURCE_API, text="Ответ не требуется"),
    event(3, "chat-a", 40, CHAT_SENDER_CLIENT, "ios", text="Товар сломался"),
    event(4, "chat-b", 5, CHAT_SENDER_SELLER, CHAT_SOURCE_PORTAL, prompt=True, text="Здравствуйте. Вы оставили отзыв"),
    event(5, "chat-b", 6, CHAT_SENDER_SELLER, CHAT_SOURCE_API, text="Ответ не требуется"),
    event(6, "chat-c", 9, CHAT_SENDER_SELLER, CHAT_SOURCE_PORTAL, prompt=True, text="Здравствуйте. Вы оставили отзыв"),
]


@pytest_asyncio.fixture
async def application() -> AsyncIterator[Application]:
    application = Application(load_settings("backend/shared/settings/config.test.yaml"))
    app = application.get_app()
    async with app.router.lifespan_context(app):
        yield application


@pytest_asyncio.fixture
async def seller(application: Application) -> AsyncIterator[uuid.UUID]:
    async with application.database.session() as session:
        model = SellerModel(name="ИП Чаты", catalog_sync_status="success", egress_status="verified")
        session.add(model)
        await session.flush()
        seller_id = model.id
        mirror = MirrorRepository(session)
        await mirror.record_attempt(seller_id, MIRROR_CHATS, now=DAY)
        await mirror.insert_chat_events(seller_id, FEED, now=DAY)
        # Курсор — время последнего прочитанного события: лента дочитана до сейчас.
        read_at = datetime.now(UTC)
        await mirror.save_chat_cursor(seller_id, int(read_at.timestamp() * 1000), tail_reached_at=read_at)
        await mirror.mark_collected(seller_id, MIRROR_CHATS, now=read_at)
        await session.commit()
    try:
        yield seller_id
    finally:
        async with application.database.session() as session:
            await session.execute(delete(TrackedSellerModel).where(TrackedSellerModel.seller_id == seller_id))
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


async def test_a_seller_that_is_not_connected_has_no_report(client: AsyncClient, seller: uuid.UUID) -> None:
    assert (await client.get(f"{API}/sellers/{seller}")).status_code == 404


async def test_the_report_counts_replies_with_and_without_our_message(client: AsyncClient, seller: uuid.UUID) -> None:
    assert (await client.post(AUTOMATION, json={"seller_id": str(seller)})).status_code == 201

    body = (await client.get(f"{API}/sellers/{seller}")).json()

    assert body["followed"] == {
        "total": 2,
        "replied": 1,
        "silent": 1,
        "pending": 0,
        "reply_rate": 0.5,
        "silent_rate": 0.5,
        "pending_rate": 0.0,
    }
    # Без нашего сообщения и без ответа после запуска — пропуск рассылки, не контрольная группа.
    assert (body["missed"]["total"], body["missed"]["silent"]) == (1, 1)
    assert (body["early"]["total"], body["before"]["total"]) == (0, 0)
    assert (body["after_launch"]["total"], body["after_launch"]["reply_rate"]) == (3, 1 / 3)
    assert body["launch_at"] == FEED[3].added_at.isoformat()
    # Окно «до запуска» — две недели до дня запуска: туда попал старый диалог с ответом менеджера.
    assert (body["baseline"]["summary"]["total"], body["baseline"]["summary"]["silent"]) == (1, 1)
    assert body["baseline"]["date_to"] == (DAY - timedelta(days=1)).date().isoformat()
    assert body["reply_window_hours"] == 48
    assert [(day["total"]["total"], day["followed"]["total"], day["missed"]["total"]) for day in body["days"]] == [
        (3, 2, 1)
    ]
    assert body["synced_through"] is not None and body["collection_error"] is None
    assert [dialog["chat_id"] for dialog in body["dialogs"]] == ["chat-c", "chat-b", "chat-a"]
    assert body["dialogs"][2]["reply_text"] == "Товар сломался"

    replied = (await client.get(f"{API}/sellers/{seller}", params={"group": "followed", "outcome": "replied"})).json()
    assert (replied["total_dialogs"], replied["followed"]["total"]) == (1, 2)


async def test_silence_is_not_a_verdict_beyond_what_the_feed_has_been_read_to(
    client: AsyncClient, seller: uuid.UUID, application: Application
) -> None:
    """Воркер отстал на сутки: непрочитанные ответы не должны превращаться в «не ответил»."""
    await client.post(AUTOMATION, json={"seller_id": str(seller)})
    async with application.database.session() as session:
        await MirrorRepository(session).save_chat_cursor(
            seller, int((DAY + timedelta(hours=1)).timestamp() * 1000), tail_reached_at=None
        )
        await session.commit()

    body = (await client.get(f"{API}/sellers/{seller}")).json()

    assert (body["followed"]["replied"], body["followed"]["silent"], body["followed"]["pending"]) == (1, 0, 1)


async def test_a_bad_period_or_filter_is_refused(client: AsyncClient, seller: uuid.UUID) -> None:
    await client.post(AUTOMATION, json={"seller_id": str(seller)})

    reversed_period = {"date_from": "2026-09-10", "date_to": "2026-09-01"}
    assert (await client.get(f"{API}/sellers/{seller}", params=reversed_period)).status_code == 422
    assert (await client.get(f"{API}/sellers/{seller}", params={"group": "nope"})).status_code == 422
    too_long = {"date_from": "2026-01-01", "date_to": "2026-09-01"}
    assert (await client.get(f"{API}/sellers/{seller}", params=too_long)).status_code == 422


async def test_the_export_has_the_summary_and_every_dialog(client: AsyncClient, seller: uuid.UUID) -> None:
    await client.post(AUTOMATION, json={"seller_id": str(seller)})

    response = await client.get(f"{API}/sellers/{seller}/export")

    assert response.status_code == 200
    assert "review_chats_" in response.headers["content-disposition"]
    workbook = load_workbook(io.BytesIO(response.content))
    summary, dialogs = workbook["Сводка"], workbook["Диалоги"]
    assert [cell.value for cell in summary[5]][:4] == ["С нашим сообщением", 2, 1, 1]
    assert [cell.value for cell in summary[7]][:3] == ["Пропуск рассылки", 1, 0]
    assert dialogs.max_row == 4
    assert dialogs["H4"].value == "Товар сломался"
