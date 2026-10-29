# ruff: noqa: F811 — фикстуры общие с тестами страницы и подставляются по имени.
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from backend.app.factory import Application
from backend.modules.wb_core.domain import MIRROR_CHATS
from backend.modules.wb_core.infrastructure.postgres import MirrorRepository
from backend.modules.wb_support_bot.infrastructure.postgres import (
    SEND_DRY_RUN,
    SEND_SENT,
    DialogModel,
    ReplyVerdictModel,
    SendModel,
)
from backend.tests.wb_support_bot.test_support_bot_http import (  # noqa: F401
    API,
    AUTOMATION,
    application,
    client,
    seller,
)
from backend.tests.wb_support_bot.test_support_bot_processor import CHAT, PROMPT_AT, event

REPLY_TEXT = "не буду ничего удалять"
NOW = datetime.now(UTC)


async def seed(
    application: Application, seller_id: uuid.UUID, *, category: str = "threat", state: str = "awaiting_us"
) -> None:
    first_send = NOW - timedelta(hours=60)
    reply_at = first_send + timedelta(hours=3)
    async with application.database.session() as session:
        mirror = MirrorRepository(session)
        await mirror.record_attempt(seller_id, MIRROR_CHATS, now=NOW)
        buyer = replace(event(0, "buyer", REPLY_TEXT), added_at=reply_at, event_id="buyer-reply")
        prompt = replace(event(0, "prompt", "Автосообщение"), added_at=PROMPT_AT)
        await mirror.insert_chat_events(seller_id, [prompt, buyer], now=NOW)
        session.add(
            DialogModel(
                seller_id=seller_id,
                chat_id=CHAT,
                prompt_at=PROMPT_AT,
                state=state,
                nm_id=555,
                campaigns_count=1,
                sent_count=1,
                no_contact=False,
                attention=False,
                excluded=False,
                closed=False,
                reply_first_at=reply_at,
            )
        )
        session.add(
            SendModel(
                seller_id=seller_id,
                chat_id=CHAT,
                prompt_at=PROMPT_AT,
                seq=1,
                text="Напоминание",
                status=SEND_SENT,
                planned_at=first_send,
                sent_at=first_send,
            )
        )
        session.add(
            SendModel(
                seller_id=seller_id,
                chat_id=CHAT,
                prompt_at=PROMPT_AT,
                seq=2,
                text="Напоминание",
                status=SEND_DRY_RUN,
                planned_at=first_send + timedelta(hours=1),
            )
        )
        session.add(
            ReplyVerdictModel(
                id=uuid.uuid4(),
                seller_id=seller_id,
                chat_id=CHAT,
                prompt_at=PROMPT_AT,
                slice_fingerprint="a" * 40,
                first_event_id="buyer-reply",
                last_event_id="buyer-reply",
                first_event_at=reply_at,
                last_event_at=reply_at,
                messages=1,
                prompt_version="r2",
                status="done",
                category=category,
                reason="угрожает судом",
                model="qwen",
                tokens_in=800,
                tokens_out=30,
                attempts=1,
                requested_at=reply_at,
                completed_at=reply_at + timedelta(minutes=12),
            )
        )
        await session.commit()


async def connect(client: AsyncClient, seller: uuid.UUID) -> None:
    assert (await client.post(AUTOMATION, json={"seller_id": str(seller)})).status_code == 201


async def test_the_summary_has_the_effect_the_classes_and_no_texts(
    application: Application, client: AsyncClient, seller: uuid.UUID
) -> None:
    await connect(client, seller)
    await seed(application, seller)

    response = await client.get(f"{API}/sellers/{seller}/replies/summary", params={"days": 14})

    body = response.json()
    assert response.status_code == 200
    assert body["gate_mode"] == "off" and body["live_since"] is None
    assert body["effect"]["live"]["replied48"] == 1 and body["effect"]["control"]["dialogs"] == 0
    assert body["categories"] == [{"category": "threat", "count": 1}]
    assert body["actions"] == {"continue": 0, "stop": 1, "wait": 0}
    assert body["escalations_open"] == 1 and body["health"]["calls"] == 1
    assert REPLY_TEXT not in response.text


async def test_the_switch_to_live_is_remembered_for_the_chart(client: AsyncClient, seller: uuid.UUID) -> None:
    await connect(client, seller)
    assert (await client.put(f"{API}/sellers/{seller}/mode", json={"mode": "live"})).status_code == 204

    body = (await client.get(f"{API}/sellers/{seller}/replies/summary")).json()

    assert body["live_since"] is not None


async def test_an_open_escalation_lists_the_chat_and_goes_away_once_closed(
    application: Application, client: AsyncClient, seller: uuid.UUID
) -> None:
    await connect(client, seller)
    await seed(application, seller)

    items = (await client.get(f"{API}/sellers/{seller}/replies/escalations")).json()["items"]

    assert len(items) == 1 and items[0]["category"] == "threat" and items[0]["nm_id"] == 555
    assert items[0]["reason"] == "угрожает судом" and "chatId" in (items[0]["chat_url"] or "")
    assert REPLY_TEXT not in str(items)
    flags = {"chat_id": CHAT, "prompt_at": PROMPT_AT.isoformat(), "closed": True}
    assert (await client.post(f"{API}/sellers/{seller}/dialogs/flags", json=flags)).status_code == 204
    assert (await client.get(f"{API}/sellers/{seller}/replies/escalations")).json()["items"] == []


async def test_the_text_is_given_only_by_the_dialog_card(
    application: Application, client: AsyncClient, seller: uuid.UUID
) -> None:
    await connect(client, seller)
    await seed(application, seller)

    card = await client.get(
        f"{API}/sellers/{seller}/replies/dialog", params={"chat_id": CHAT, "prompt_at": PROMPT_AT.isoformat()}
    )

    body = card.json()
    assert card.status_code == 200
    assert [message["role"] for message in body["messages"]] == ["wb", "buyer"]
    assert body["messages"][1]["text"] == REPLY_TEXT
    assert body["verdicts"][0]["category"] == "threat" and body["verdicts"][0]["action"] == "stop"


async def test_the_dialog_list_shows_the_class_of_the_last_reply(
    application: Application, client: AsyncClient, seller: uuid.UUID
) -> None:
    await connect(client, seller)
    await seed(application, seller, category="refuses")

    [item] = (await client.get(f"{API}/sellers/{seller}/dialogs")).json()["dialogs"]

    assert item["reply_category"] == "refuses" and item["reply_hold"] is False


async def test_bad_requests_are_refused(application: Application, client: AsyncClient, seller: uuid.UUID) -> None:
    assert (await client.get(f"{API}/sellers/{seller}/replies/summary")).status_code == 404
    await connect(client, seller)

    assert (await client.get(f"{API}/sellers/{seller}/replies/summary", params={"days": 0})).status_code == 422
    unknown = {"chat_id": "1:none", "prompt_at": PROMPT_AT.isoformat()}
    assert (await client.get(f"{API}/sellers/{seller}/replies/dialog", params=unknown)).status_code == 422
    broken = {"chat_id": CHAT, "prompt_at": "вчера"}
    assert (await client.get(f"{API}/sellers/{seller}/replies/dialog", params=broken)).status_code == 422
