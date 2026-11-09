# ruff: noqa: F811 — фикстуры `database` и `seller` общие с тестами процессора и подставляются по имени.
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from backend.modules.wb_support_bot.domain import (
    MANUAL_ALLOW,
    MANUAL_DENY,
    REASON_EXCLUDED,
    REASON_MANUAL_OFF,
    REASON_MANUAL_ON,
    REASON_MODEL_OFF,
    REASON_NO_CONTACT,
    REASON_PRODUCT_OFF,
    REASON_REPLY_HOLD,
    REASON_STATE,
    REASON_VERDICT_CLOSING,
    REASON_VERDICT_OPEN,
    REASON_VERDICT_PENDING,
    REASON_VERDICT_UNCLEAR,
    SOURCE_EXCLUDED,
    SOURCE_MANUAL,
    SOURCE_MODEL,
    SOURCE_PRODUCT,
    SOURCE_SYSTEM,
    Mailing,
    decide,
    manual_valid,
)
from backend.modules.wb_support_bot.infrastructure.postgres import (
    NO_ARTICLE_KEY,
    VERDICT_PENDING,
    DialogKey,
    SupportBotRepository,
)
from backend.storage.pg import Database
from backend.tests.wb_support_bot.test_support_bot_processor import (  # noqa: F401  (фикстуры и помощники общие)
    BASE_FEED,
    NOW,
    FakeSender,
    clean_classify_outbox,
    database,
    dialog,
    feed,
    go_live,
    run_with,
    seller,
    sends,
    verdicts,
)
from backend.tests.wb_support_bot.test_support_bot_processor import decide as set_verdict
from backend.tests.wb_support_bot.test_support_bot_reply_gate import (  # noqa: F401
    REPLY,
    buyer_replies,
    clean_reply_outbox,
    outbox_count,
    rows_of,
    start_campaign,
)
from backend.tests.wb_support_bot.test_support_bot_verdicts import (
    FakeLLM,
    consumer,
    deliver,
    request,
    stored,
)

PRODUCT = 1304195061  # артикул из автосообщения в тестовой ленте


def mailing(**override: object) -> Mailing:
    values: dict[str, object] = {
        "mode": "enforce",
        "state": "in_campaign",
        "no_contact": False,
        "excluded": False,
        "reply_hold": False,
        "product_enabled": True,
        "verdict": "open",
        "manual": None,
        "manual_until": None,
    }
    values.update(override)
    return decide(**values)  # type: ignore[arg-type]


# ---- домен: итоговое решение ----


@pytest.mark.parametrize(
    ("verdict", "on", "reason"),
    [
        ("open", True, REASON_VERDICT_OPEN),
        ("closing", False, REASON_VERDICT_CLOSING),
        ("unclear", False, REASON_VERDICT_UNCLEAR),
        (None, False, REASON_VERDICT_PENDING),
    ],
)
def test_the_toggle_follows_the_model_until_a_person_touches_it(verdict: str | None, on: bool, reason: str) -> None:
    result = mailing(verdict=verdict)

    assert (result.on, result.source, result.reason, result.locked) == (on, SOURCE_MODEL, reason, False)


def test_with_the_model_off_the_dialog_is_written_to() -> None:
    result = mailing(mode="off", verdict=None)

    assert result.on and result.reason == REASON_MODEL_OFF


@pytest.mark.parametrize("state", ["in_campaign", "awaiting_buyer"])
def test_the_toggle_exists_while_the_campaign_runs_or_is_about_to_start(state: str) -> None:
    assert mailing(state=state).locked is False


@pytest.mark.parametrize("state", ["no_reply", "awaiting_us", "exhausted", "closed", "product_removed"])
def test_other_states_have_no_mailing_and_the_toggle_is_locked(state: str) -> None:
    result = mailing(state=state)

    assert (result.on, result.source, result.reason, result.locked) == (False, SOURCE_SYSTEM, REASON_STATE, True)


def test_a_manual_choice_beats_the_model_in_both_directions() -> None:
    until = NOW + timedelta(hours=24)

    forced_on = mailing(verdict="closing", manual=MANUAL_ALLOW, manual_until=until)
    forced_off = mailing(verdict="open", manual=MANUAL_DENY, manual_until=until)

    assert (forced_on.on, forced_on.source, forced_on.reason, forced_on.manual_until) == (
        True,
        SOURCE_MANUAL,
        REASON_MANUAL_ON,
        until,
    )
    assert (forced_off.on, forced_off.reason) == (False, REASON_MANUAL_OFF)


def test_a_disabled_product_beats_everything_and_locks_the_toggle() -> None:
    result = mailing(product_enabled=False, manual=MANUAL_ALLOW, verdict="open")

    assert (result.on, result.source, result.reason, result.locked) == (False, SOURCE_PRODUCT, REASON_PRODUCT_OFF, True)


def test_an_excluded_dialog_is_off_but_can_be_switched_back_on() -> None:
    result = mailing(excluded=True, manual=MANUAL_ALLOW, verdict="open")

    assert (result.on, result.source, result.reason, result.locked) == (False, SOURCE_EXCLUDED, REASON_EXCLUDED, False)


def test_a_request_not_to_write_is_never_overridden() -> None:
    result = mailing(no_contact=True, manual=MANUAL_ALLOW, verdict="open")

    assert (result.on, result.reason, result.locked) == (False, REASON_NO_CONTACT, True)


def test_a_reply_that_is_still_being_judged_holds_the_dialog() -> None:
    result = mailing(reply_hold=True, manual=MANUAL_ALLOW, verdict="open")

    assert (result.on, result.reason, result.locked) == (False, REASON_REPLY_HOLD, True)


def test_a_manual_choice_holds_until_the_tail_changes_or_the_time_runs_out() -> None:
    until = NOW + timedelta(hours=1)
    base = {"manual": MANUAL_ALLOW, "manual_fingerprint": "a", "tail_fingerprint": "a"}

    assert manual_valid(**base, manual_until=until, now=NOW)
    assert not manual_valid(**base, manual_until=until, now=until)
    assert not manual_valid(**base, manual_until=until, now=until + timedelta(seconds=1))
    assert not manual_valid(**{**base, "tail_fingerprint": "b"}, manual_until=until, now=NOW)
    assert not manual_valid(None, manual_fingerprint=None, manual_until=None, tail_fingerprint=None, now=NOW)
    assert manual_valid(**base, manual_until=None, now=NOW + timedelta(days=30))


# ---- бот: выключенный товар ----


async def switch_product(
    database: Database, seller_id: uuid.UUID, key: int, enabled: bool, *, now: datetime = NOW
) -> None:
    async with database.session() as session:
        await SupportBotRepository(session).set_product_enabled(seller_id, key, enabled, now=now)
        await session.commit()


async def test_a_disabled_product_gets_no_messages_and_its_dialogs_are_not_sent_to_the_model(
    database: Database, seller: uuid.UUID
) -> None:
    await go_live(database, seller)
    await switch_product(database, seller, PRODUCT, False)
    await feed(database, seller, BASE_FEED, read_at=NOW)

    report = await run_with(database, seller, FakeSender(), "enforce")
    later = await run_with(database, seller, FakeSender(), "enforce", now=NOW + timedelta(minutes=11))

    assert report.dialogs == 1 and report.sent == 0 and report.held == 0 and report.verdicts_requested == 0
    assert later.verdicts_requested == 0 and later.sent == 0
    assert await verdicts(database, seller) == [] and await sends(database, seller) == []
    assert (await dialog(database, seller)).state == "awaiting_buyer", "кампанию по выключенному товару не стартуем"


async def test_turning_the_product_back_on_asks_the_model_and_then_starts_the_campaign(
    database: Database, seller: uuid.UUID
) -> None:
    await go_live(database, seller)
    await switch_product(database, seller, PRODUCT, False)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    await run_with(database, seller, FakeSender(), "enforce")
    enabled_at = NOW + timedelta(minutes=10)

    await switch_product(database, seller, PRODUCT, True, now=enabled_at)

    assert (await dialog(database, seller)).next_send_at is None
    sender = FakeSender()
    resumed = await run_with(database, seller, sender, "enforce", now=NOW + timedelta(minutes=11))
    assert resumed.verdicts_requested == 1 and resumed.campaigns_started == 0 and resumed.sent == 0
    await set_verdict(database, seller, "open")
    later = NOW + timedelta(hours=3)
    await feed(database, seller, BASE_FEED, read_at=later)
    sent = await run_with(database, seller, sender, "enforce", now=later)
    assert sent.sent == 1 and sender.calls[0][1] == "1:sign"


async def test_a_product_switch_is_per_seller_and_per_article(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await switch_product(database, seller, PRODUCT + 1, False)
    await feed(database, seller, BASE_FEED, read_at=NOW)

    report = await run_with(database, seller, FakeSender(), "enforce")

    assert report.verdicts_requested == 1, "выключен другой артикул, этот работает"


async def test_dialogs_without_an_article_share_the_zero_key(database: Database, seller: uuid.UUID) -> None:
    async with database.session() as session:
        repository = SupportBotRepository(session)
        await repository.set_product_enabled(seller, NO_ARTICLE_KEY, False)
        await session.commit()
        assert await repository.disabled_products(seller) == {NO_ARTICLE_KEY}


async def test_a_reply_to_a_disabled_product_is_not_sent_to_the_model_and_waits(
    database: Database, seller: uuid.UUID
) -> None:
    await start_campaign(database, seller, "enforce")
    await switch_product(database, seller, PRODUCT, False)

    await buyer_replies(database, seller, "enforce", REPLY)

    state = await dialog(database, seller)
    assert state.state == "in_campaign" and state.reply_hold, "без оценки ответа диалог не закрываем"
    assert await rows_of(database, seller) == [] and await outbox_count(database) == 0


async def test_turning_the_product_back_on_judges_the_waiting_reply(database: Database, seller: uuid.UUID) -> None:
    await start_campaign(database, seller, "enforce")
    await switch_product(database, seller, PRODUCT, False)
    await buyer_replies(database, seller, "enforce", REPLY)
    await switch_product(database, seller, PRODUCT, True)

    await buyer_replies(database, seller, "enforce", REPLY, minutes=45)

    assert len(await rows_of(database, seller)) == 1 and await outbox_count(database) == 1


# ---- бот: срок ручной отметки ----


async def dialog_key(database: Database, seller_id: uuid.UUID) -> DialogKey:
    state = await dialog(database, seller_id)
    return DialogKey(seller_id, state.chat_id, state.prompt_at)


async def mark(database: Database, key: DialogKey, value: str | None, until: datetime | None) -> None:
    async with database.session() as session:
        await SupportBotRepository(session).set_manual_verdict(key, value, until=until)
        await session.commit()


async def closed_dialog(database: Database, seller_id: uuid.UUID) -> DialogKey:
    await go_live(database, seller_id)
    await feed(database, seller_id, BASE_FEED, read_at=NOW)
    await run_with(database, seller_id, FakeSender(), "enforce")
    await set_verdict(database, seller_id, "closing")
    return await dialog_key(database, seller_id)


async def test_a_manual_allow_lets_the_message_through_until_it_expires(database: Database, seller: uuid.UUID) -> None:
    key = await closed_dialog(database, seller)
    await mark(database, key, MANUAL_ALLOW, NOW + timedelta(hours=24))
    sender = FakeSender()

    report = await run_with(database, seller, sender, "enforce", now=NOW + timedelta(minutes=11))

    assert report.sent == 1 and report.held == 0


async def test_an_expired_manual_choice_is_dropped_and_the_model_decides_again(
    database: Database, seller: uuid.UUID
) -> None:
    key = await closed_dialog(database, seller)
    await mark(database, key, MANUAL_ALLOW, NOW + timedelta(minutes=5))
    sender = FakeSender()
    run_at = NOW + timedelta(minutes=11)
    await feed(database, seller, BASE_FEED, read_at=run_at)

    report = await run_with(database, seller, sender, "enforce", now=run_at)

    assert report.sent == 0 and report.campaigns_started == 0 and sender.calls == []
    state = await dialog(database, seller)
    assert state.manual_verdict is None and state.manual_until is None


async def test_a_manual_deny_stops_a_dialog_the_model_would_write_to(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    await run_with(database, seller, FakeSender(), "enforce")
    await set_verdict(database, seller, "open")
    await mark(database, await dialog_key(database, seller), MANUAL_DENY, NOW + timedelta(hours=24))

    report = await run_with(database, seller, FakeSender(), "enforce", now=NOW + timedelta(minutes=11))

    assert report.sent == 0 and report.campaigns_started == 0


# ---- потребители: выключенный товар не уходит на оценку ----


async def test_the_classify_consumer_skips_a_dialog_of_a_disabled_product(
    database: Database, seller: uuid.UUID
) -> None:
    await feed(database, seller, BASE_FEED, read_at=NOW)
    await run_with(database, seller, FakeSender(), "off")
    await switch_product(database, seller, PRODUCT, False)
    verdict_id = await request(database, seller, tail_event_id="specialist-60")
    llm = FakeLLM()

    await deliver(consumer(database, llm), verdict_id, seller, uuid.uuid4())

    assert llm.calls == []
    row = await stored(database, verdict_id)
    assert row.status == VERDICT_PENDING and row.verdict is None


async def test_the_classify_consumer_still_works_for_an_enabled_product(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, BASE_FEED, read_at=NOW)
    await run_with(database, seller, FakeSender(), "off")
    verdict_id = await request(database, seller, tail_event_id="specialist-60")
    llm = FakeLLM()

    await deliver(consumer(database, llm), verdict_id, seller, uuid.uuid4())

    assert len(llm.calls) == 1 and (await stored(database, verdict_id)).verdict == "open"


async def test_stuck_verdicts_of_a_disabled_product_do_not_turn_the_monitor_red(
    database: Database, seller: uuid.UUID
) -> None:
    await feed(database, seller, BASE_FEED, read_at=NOW)
    await run_with(database, seller, FakeSender(), "off")
    await request(database, seller, tail_event_id="specialist-60")
    later = datetime.now(UTC) + timedelta(hours=2)  # requested_at строки ставит база: настоящее «сейчас»

    async def stuck() -> int:
        async with database.session() as session:
            return await SupportBotRepository(session).verdicts_pending_stuck(seller, later)

    assert await stuck() == 1
    await switch_product(database, seller, PRODUCT, False)
    assert await stuck() == 0
    await switch_product(database, seller, PRODUCT, True)
    assert await stuck() == 1
