# ruff: noqa: F811 — фикстуры `database` и `seller` общие с тестами процессора и подставляются по имени.
import random
import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update

from backend.modules.wb_core.application import ChatMirror
from backend.modules.wb_core.domain import ChatEvent
from backend.modules.wb_core.infrastructure.postgres.models import OutboxEventModel
from backend.modules.wb_support_bot.application import BotPolicy, SupportBotProcessor
from backend.modules.wb_support_bot.domain import (
    CONTINUE_CATEGORIES,
    GUARD_ATTACHMENT,
    GUARD_STOPWORD,
    REPLY_CATEGORIES,
    CampaignRules,
    DialogState,
    Gate,
    SentMessage,
    gate_decision,
    is_wb_notice,
    parse_reply_verdict,
    project_dialog,
    reply_slice,
    reply_user_message,
    slice_fingerprint,
    slice_guard,
)
from backend.modules.wb_support_bot.infrastructure.postgres import (
    VERDICT_DONE,
    VERDICT_FAILED,
    VERDICT_PENDING,
    DialogModel,
    ReplyVerdictModel,
    SendModel,
    SupportBotRepository,
)
from backend.shared.kafka_streams.topics import SupportBotTopics
from backend.shared.llm import LLMError, LLMResult
from backend.storage.pg import Database
from backend.tests.wb_support_bot.test_support_bot_processor import (  # noqa: F401  (фикстуры и помощники общие)
    BASE_FEED,
    NOW,
    PROMPT_AT,
    FakeSender,
    database,
    event,
    feed,
    no_sleep,
    seller,
)
from backend.workers.wb_support_bot.reply_consumer import InvalidPayloadError, ReplyConsumer

NOTICE = "Мы удалили это сообщение — оно нарушает правила Wildberries"
RULES = CampaignRules(max_idle=timedelta(days=5))
STARTED = PROMPT_AT + timedelta(minutes=370)
REPLY_AT = 330 + 60  # минуты от автосообщения: покупатель отвечает через 20 минут после старта кампании
REPLY = event(REPLY_AT, "buyer", "не буду я ничего удалять")


@pytest_asyncio.fixture(autouse=True)
async def clean_reply_outbox(database: Database) -> AsyncIterator[None]:
    yield
    async with database.session() as session:
        await session.execute(
            delete(OutboxEventModel).where(OutboxEventModel.topic == SupportBotTopics.REPLY_REQUESTED)
        )
        await session.commit()


# ---- домен ----


def test_the_slice_is_the_unresolved_buyer_messages_after_the_boundary() -> None:
    events = [
        *BASE_FEED,
        event(400, "buyer", "первое"),
        event(410, "bot", "Напоминание бота"),
        event(420, "buyer", "второе"),
        event(430, "buyer", NOTICE),
    ]

    after_start = reply_slice(events, boundary=STARTED)
    after_first = reply_slice(events, boundary=PROMPT_AT + timedelta(minutes=400))

    assert [item.event_id for item in after_start] == ["buyer-400", "buyer-420"]
    assert [item.event_id for item in after_first] == ["buyer-420"]


def test_a_bot_message_does_not_move_the_boundary() -> None:
    """Покупатель ответил, а напоминание бота появилось в ленте следом из-за лага зеркала."""
    events = [*BASE_FEED, event(400, "buyer", "не хочу"), event(417, "bot", "Напоминание бота")]

    assert [item.event_id for item in reply_slice(events, boundary=STARTED)] == ["buyer-400"]


def test_the_fingerprint_follows_the_messages_and_their_text() -> None:
    one = [event(400, "buyer", "не хочу")]
    edited = [event(400, "buyer", "не хочу совсем")]
    more = [*one, event(410, "buyer", "и не буду")]

    assert slice_fingerprint(one) == slice_fingerprint([event(400, "buyer", "не хочу")])
    assert len({slice_fingerprint(one), slice_fingerprint(edited), slice_fingerprint(more)}) == 3


def test_a_stop_word_or_an_attachment_stops_the_campaign_without_the_model() -> None:
    attached = replace(event(400, "buyer", "вот скрин"), has_attachments=True)

    assert slice_guard([event(400, "buyer", "не пишите мне больше")]) == GUARD_STOPWORD
    assert slice_guard([event(400, "buyer", "подам жалобу")]) == GUARD_STOPWORD
    assert slice_guard([attached]) == GUARD_ATTACHMENT
    assert slice_guard([event(400, "buyer", "не хочу")]) is None


def test_the_wb_notice_is_recognised_for_both_senders() -> None:
    assert is_wb_notice(event(400, "buyer", NOTICE)) and is_wb_notice(event(400, "specialist", NOTICE))
    assert not is_wb_notice(event(400, "buyer", "Вы удалили"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"category": "refuses", "reason": "не хочет"}', ("refuses", "не хочет")),
        ('Вот ответ: {"category": "THREAT", "reason": "суд"}', ("threat", "суд")),
        ('{"category": "angry", "reason": "x"}', ("unclear", "x")),
        ("не знаю", ("unclear", "ответ модели без JSON")),
        ("{oops}", ("unclear", "ответ модели не разобран")),
        ("[1]", ("unclear", "ответ модели без JSON")),
    ],
)
def test_anything_unreadable_becomes_unclear(raw: str, expected: tuple[str, str]) -> None:
    assert parse_reply_verdict(raw) == expected


def test_only_the_three_agreed_classes_continue_the_campaign() -> None:
    assert {"refuses", "not_helped", "noise"} == CONTINUE_CATEGORIES
    assert len(REPLY_CATEGORIES) == 12
    for category in REPLY_CATEGORIES:
        expected = Gate.CONTINUE if category in CONTINUE_CATEGORIES else Gate.STOP
        assert gate_decision(guard=None, status="done", category=category) is expected


@pytest.mark.parametrize(
    ("guard", "status", "category", "expected"),
    [
        (GUARD_STOPWORD, "pending", None, Gate.STOP),
        (GUARD_ATTACHMENT, "done", None, Gate.STOP),
        (None, "pending", None, Gate.WAIT),
        (None, "failed", None, Gate.WAIT),
        (None, None, None, Gate.WAIT),
        (None, "done", "refuses", Gate.CONTINUE),
        (None, "done", None, Gate.STOP),
    ],
)
def test_no_verdict_waits_and_never_stops_the_campaign(
    guard: str | None, status: str | None, category: str | None, expected: Gate
) -> None:
    assert gate_decision(guard=guard, status=status, category=category) is expected


def test_the_request_has_context_and_numbered_messages() -> None:
    text = reply_user_message([("продавец", "Опишите проблему"), ("напоминание бота", "Как дела?")], ["не хочу", "."])

    assert "[продавец] Опишите проблему" in text and "[напоминание бота] Как дела?" in text
    assert "1. не хочу" in text and "2. ." in text


def _project(events: list[ChatEvent], **kwargs):  # type: ignore[no-untyped-def]
    return project_dialog(
        events,
        kwargs.pop("sent", []),
        now=STARTED + timedelta(hours=3),
        rules=RULES,
        campaign_started_at=STARTED,
        **kwargs,
    )


def test_a_reply_in_the_campaign_stops_it_unless_it_was_cleared() -> None:
    events = [*BASE_FEED, event(400, "buyer", "не хочу")]
    reply_at = PROMPT_AT + timedelta(minutes=400)

    assert _project(events).state is DialogState.AWAITING_US
    assert _project(events, cleared_through=reply_at).state is DialogState.IN_CAMPAIGN
    assert _project(events, reply_pending=True).state is DialogState.IN_CAMPAIGN


def test_a_new_reply_after_a_cleared_one_stops_the_campaign_again() -> None:
    events = [*BASE_FEED, event(400, "buyer", "не хочу"), event(500, "buyer", "угрожаю судом")]
    cleared = PROMPT_AT + timedelta(minutes=400)

    assert _project(events, cleared_through=cleared).state is DialogState.AWAITING_US


def test_the_real_last_buyer_message_survives_the_clearing() -> None:
    events = [*BASE_FEED, event(400, "buyer", "не хочу")]

    projection = _project(events, cleared_through=PROMPT_AT + timedelta(minutes=400))

    assert projection.buyer_last_at == PROMPT_AT + timedelta(minutes=400)


def test_the_wb_notice_from_the_seller_side_ends_the_campaign_only_when_notices_count() -> None:
    events = [*BASE_FEED, event(400, "specialist", NOTICE)]

    assert _project(events).state is DialogState.AWAITING_BUYER
    assert _project(events, ignore_notices=True).state is DialogState.IN_CAMPAIGN


def test_a_reply_made_only_of_a_wb_notice_is_not_a_reply() -> None:
    events = [*BASE_FEED, event(400, "buyer", NOTICE)]

    assert _project(events, ignore_notices=True).state is DialogState.IN_CAMPAIGN


def test_the_bot_message_after_a_reply_does_not_hide_the_reply() -> None:
    events = [*BASE_FEED, event(400, "buyer", "не хочу"), event(417, "bot", "Напоминание")]
    sent = [SentMessage(PROMPT_AT + timedelta(minutes=417), "Напоминание")]

    assert _project(events, sent=sent).state is DialogState.AWAITING_US


# ---- процессор ----


async def go(database: Database, seller_id: uuid.UUID, *, mode: str, now: datetime, **policy: object):  # type: ignore[no-untyped-def]
    async with database.session() as session:
        processor = SupportBotProcessor(
            SupportBotRepository(session),
            ChatMirror(session),
            FakeSender(),
            BotPolicy(reply_gate_mode=mode, **policy),  # type: ignore[arg-type]
            rng=random.Random(1),
            sleep=no_sleep,
        )
        return await processor.process_seller(seller_id, now=now)


async def dialog_of(database: Database, seller_id: uuid.UUID) -> DialogModel:
    async with database.session() as session:
        return (await session.scalars(select(DialogModel).where(DialogModel.seller_id == seller_id))).one()


async def rows_of(database: Database, seller_id: uuid.UUID) -> list[ReplyVerdictModel]:
    async with database.session() as session:
        return list(
            await session.scalars(
                select(ReplyVerdictModel)
                .where(ReplyVerdictModel.seller_id == seller_id)
                .order_by(ReplyVerdictModel.first_event_at)
            )
        )


async def send_count(database: Database, seller_id: uuid.UUID) -> int:
    async with database.session() as session:
        return len(list(await session.scalars(select(SendModel).where(SendModel.seller_id == seller_id))))


async def finish(database: Database, row_id: uuid.UUID, category: str | None, *, status: str = VERDICT_DONE) -> None:
    async with database.session() as session:
        await session.execute(
            update(ReplyVerdictModel).where(ReplyVerdictModel.id == row_id).values(status=status, category=category)
        )
        await session.commit()


async def start_campaign(database: Database, seller_id: uuid.UUID, mode: str) -> None:
    await feed(database, seller_id, BASE_FEED, read_at=NOW)
    await go(database, seller_id, mode=mode, now=NOW)
    state = await dialog_of(database, seller_id)
    assert state.state == "in_campaign" and state.campaign_started_at is not None


async def buyer_replies(
    database: Database, seller_id: uuid.UUID, mode: str, *events: ChatEvent, minutes: int = 30
) -> None:
    await feed(database, seller_id, list(events), read_at=NOW + timedelta(minutes=minutes))
    await go(database, seller_id, mode=mode, now=NOW + timedelta(minutes=minutes))


async def outbox_count(database: Database) -> int:
    async with database.session() as session:
        return len(
            list(
                await session.scalars(
                    select(OutboxEventModel).where(OutboxEventModel.topic == SupportBotTopics.REPLY_REQUESTED)
                )
            )
        )


async def test_with_the_layer_off_a_reply_stops_the_campaign_as_before(database: Database, seller: uuid.UUID) -> None:
    await start_campaign(database, seller, "off")

    await buyer_replies(database, seller, "off", REPLY)

    state = await dialog_of(database, seller)
    assert state.state == "awaiting_us" and state.campaign_started_at is None and not state.reply_hold
    assert await rows_of(database, seller) == [] and await outbox_count(database) == 0


async def test_in_shadow_the_class_is_recorded_but_the_campaign_still_stops(
    database: Database, seller: uuid.UUID
) -> None:
    await start_campaign(database, seller, "shadow")

    await buyer_replies(database, seller, "shadow", REPLY)

    state = await dialog_of(database, seller)
    rows = await rows_of(database, seller)
    assert state.state == "awaiting_us" and not state.reply_hold and state.reply_cleared_through is None
    assert [row.status for row in rows] == [VERDICT_PENDING] and await outbox_count(database) == 1


async def test_a_reply_holds_the_campaign_until_the_class_arrives(database: Database, seller: uuid.UUID) -> None:
    await start_campaign(database, seller, "enforce")
    before = await send_count(database, seller)

    await buyer_replies(database, seller, "enforce", REPLY)
    later = NOW + timedelta(hours=3)
    await feed(database, seller, BASE_FEED, read_at=later)
    await go(database, seller, mode="enforce", now=later)

    state = await dialog_of(database, seller)
    rows = await rows_of(database, seller)
    assert state.state == "in_campaign" and state.reply_hold and state.campaign_started_at is not None
    assert await send_count(database, seller) == before, "пока вердикта нет, напоминания не уходят"
    assert len(rows) == 1 and rows[0].messages == 1 and rows[0].last_event_id == REPLY.event_id
    assert rows[0].prompt_version == "r3"
    # Через три часа прошло больше окна повтора: запрос поставлен второй раз, а не потерян.
    assert rows[0].attempts == 2 and await outbox_count(database) == 2


async def test_refusal_continues_the_campaign_on_the_regular_step(database: Database, seller: uuid.UUID) -> None:
    await start_campaign(database, seller, "enforce")
    await buyer_replies(database, seller, "enforce", REPLY)
    [row] = await rows_of(database, seller)
    await finish(database, row.id, "refuses")
    now = NOW + timedelta(minutes=90)

    await go(database, seller, mode="enforce", now=now)

    state = await dialog_of(database, seller)
    assert state.state == "in_campaign" and not state.reply_hold
    assert state.reply_cleared_through == REPLY.added_at
    assert state.next_send_at is not None and state.next_send_at >= now + timedelta(minutes=55)
    before = await send_count(database, seller)
    later = now + timedelta(hours=3)
    await feed(database, seller, BASE_FEED, read_at=later)
    await go(database, seller, mode="enforce", now=later)
    assert await send_count(database, seller) == before + 1, "после паузы напоминание уходит"


@pytest.mark.parametrize(("category", "flag"), [("threat", "attention"), ("stop_request", "no_contact")])
async def test_a_threat_or_a_request_to_stop_ends_the_campaign_and_marks_the_dialog(
    database: Database, seller: uuid.UUID, category: str, flag: str
) -> None:
    await start_campaign(database, seller, "enforce")
    await buyer_replies(database, seller, "enforce", REPLY)
    [row] = await rows_of(database, seller)
    await finish(database, row.id, category)

    await go(database, seller, mode="enforce", now=NOW + timedelta(minutes=90))

    state = await dialog_of(database, seller)
    assert state.state == "awaiting_us" and state.campaign_started_at is None and not state.reply_hold
    assert getattr(state, flag) is True


@pytest.mark.parametrize("category", ["thanks", "cant_delete", "waiting_seller", "question", "unclear"])
async def test_other_classes_hand_the_dialog_to_a_manager(database: Database, seller: uuid.UUID, category: str) -> None:
    await start_campaign(database, seller, "enforce")
    await buyer_replies(database, seller, "enforce", REPLY)
    [row] = await rows_of(database, seller)
    await finish(database, row.id, category)

    await go(database, seller, mode="enforce", now=NOW + timedelta(minutes=90))

    assert (await dialog_of(database, seller)).state == "awaiting_us"


async def test_a_stop_word_stops_the_campaign_without_asking_the_model(database: Database, seller: uuid.UUID) -> None:
    await start_campaign(database, seller, "enforce")

    await buyer_replies(database, seller, "enforce", event(REPLY_AT, "buyer", "не пишите мне больше"))

    state = await dialog_of(database, seller)
    [row] = await rows_of(database, seller)
    assert state.state == "awaiting_us" and state.no_contact
    assert row.guard == GUARD_STOPWORD and row.status == VERDICT_DONE and await outbox_count(database) == 0


async def test_an_attachment_stops_the_campaign_without_asking_the_model(database: Database, seller: uuid.UUID) -> None:
    await start_campaign(database, seller, "enforce")

    await buyer_replies(database, seller, "enforce", replace(REPLY, has_attachments=True))

    [row] = await rows_of(database, seller)
    assert (await dialog_of(database, seller)).state == "awaiting_us"
    assert row.guard == GUARD_ATTACHMENT and await outbox_count(database) == 0


async def test_a_model_failure_holds_the_campaign_and_the_request_is_repeated(
    database: Database, seller: uuid.UUID
) -> None:
    await start_campaign(database, seller, "enforce")
    await buyer_replies(database, seller, "enforce", REPLY)
    [row] = await rows_of(database, seller)
    await finish(database, row.id, None, status=VERDICT_FAILED)

    await go(database, seller, mode="enforce", now=NOW + timedelta(minutes=70))

    [again] = await rows_of(database, seller)
    state = await dialog_of(database, seller)
    assert state.state == "in_campaign" and state.reply_hold
    assert again.status == VERDICT_PENDING and again.attempts == 2 and await outbox_count(database) == 2


async def test_when_attempts_run_out_the_campaign_stays_held_and_is_not_killed(
    database: Database, seller: uuid.UUID
) -> None:
    await start_campaign(database, seller, "enforce")
    await buyer_replies(database, seller, "enforce", REPLY)
    [row] = await rows_of(database, seller)
    await finish(database, row.id, None, status=VERDICT_FAILED)

    await go(database, seller, mode="enforce", now=NOW + timedelta(hours=2), reply_max_attempts=1)

    state = await dialog_of(database, seller)
    [after] = await rows_of(database, seller)
    assert state.state == "in_campaign" and state.reply_hold and after.attempts == 1


async def test_a_new_prompt_version_does_not_end_a_continued_campaign(database: Database, seller: uuid.UUID) -> None:
    await start_campaign(database, seller, "enforce")
    await buyer_replies(database, seller, "enforce", REPLY)
    [row] = await rows_of(database, seller)
    await finish(database, row.id, "noise")
    await go(database, seller, mode="enforce", now=NOW + timedelta(minutes=90))

    await go(database, seller, mode="enforce", now=NOW + timedelta(minutes=100), reply_prompt_version="r4")

    state = await dialog_of(database, seller)
    assert state.state == "in_campaign" and not state.reply_hold
    assert len(await rows_of(database, seller)) == 1


async def test_a_new_reply_after_a_continued_one_gets_its_own_verdict(database: Database, seller: uuid.UUID) -> None:
    await start_campaign(database, seller, "enforce")
    await buyer_replies(database, seller, "enforce", REPLY)
    [first] = await rows_of(database, seller)
    await finish(database, first.id, "refuses")
    await go(database, seller, mode="enforce", now=NOW + timedelta(minutes=90))
    second = event(REPLY_AT + 200, "buyer", "я же сказал, нет")

    await buyer_replies(database, seller, "enforce", second, minutes=290)

    rows = await rows_of(database, seller)
    state = await dialog_of(database, seller)
    assert len(rows) == 2 and rows[1].first_event_id == second.event_id and rows[1].messages == 1
    assert state.state == "in_campaign" and state.reply_hold


async def test_a_virtual_reminder_of_a_dry_run_dialog_does_not_reopen_the_cleared_slice(
    database: Database, seller: uuid.UUID
) -> None:
    await start_campaign(database, seller, "enforce")
    await buyer_replies(database, seller, "enforce", REPLY)
    [first] = await rows_of(database, seller)
    await finish(database, first.id, "refuses")
    await go(database, seller, mode="enforce", now=NOW + timedelta(minutes=90))
    later = NOW + timedelta(hours=4)
    before = await send_count(database, seller)
    await feed(database, seller, BASE_FEED, read_at=later)
    await go(database, seller, mode="enforce", now=later)
    assert await send_count(database, seller) == before + 1, "виртуальное напоминание было"
    second = event(REPLY_AT + 300, "buyer", "нет")

    await buyer_replies(database, seller, "enforce", second, minutes=400)

    rows = await rows_of(database, seller)
    assert [row.messages for row in rows] == [1, 1], "первый ответ второй раз не оценивается"


async def test_an_excluded_dialog_gets_no_verdict(database: Database, seller: uuid.UUID) -> None:
    await start_campaign(database, seller, "enforce")
    async with database.session() as session:
        await session.execute(update(DialogModel).where(DialogModel.seller_id == seller).values(excluded=True))
        await session.commit()

    await buyer_replies(database, seller, "enforce", REPLY)

    assert await rows_of(database, seller) == [] and await outbox_count(database) == 0


async def test_the_wb_notice_from_the_seller_does_not_end_the_campaign_in_enforce(
    database: Database, seller: uuid.UUID
) -> None:
    await start_campaign(database, seller, "enforce")

    await buyer_replies(database, seller, "enforce", event(REPLY_AT, "specialist", NOTICE))

    assert (await dialog_of(database, seller)).state == "in_campaign"


async def test_the_first_reply_after_the_first_bot_message_is_remembered(database: Database, seller: uuid.UUID) -> None:
    await start_campaign(database, seller, "off")

    await buyer_replies(database, seller, "off", REPLY)

    assert (await dialog_of(database, seller)).reply_first_at == REPLY.added_at


# ---- консьюмер ----


class FakeLLM:
    def __init__(self, answer: str = '{"category": "refuses", "reason": "не хочет"}') -> None:
        self.answer = answer
        self.error: Exception | None = None
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> LLMResult:
        self.calls.append((system, user))
        if self.error is not None:
            raise self.error
        return LLMResult(self.answer, "qwen3-30b-a3b", 800, 30, 900)


def reply_consumer(database: Database, llm: FakeLLM) -> ReplyConsumer:
    return ReplyConsumer(database, "unused:9092", "test-group", llm)  # type: ignore[arg-type]


async def request_reply(database: Database, seller_id: uuid.UUID, *messages: ChatEvent) -> uuid.UUID:
    row_id = uuid.uuid4()
    async with database.session() as session:
        SupportBotRepository(session).add_reply_verdict(
            ReplyVerdictModel(
                id=row_id,
                seller_id=seller_id,
                chat_id=messages[0].chat_id,
                prompt_at=PROMPT_AT,
                slice_fingerprint="f" * 40,
                first_event_id=messages[0].event_id,
                last_event_id=messages[-1].event_id,
                first_event_at=messages[0].added_at,
                last_event_at=messages[-1].added_at,
                messages=len(messages),
                prompt_version="r3",
                status=VERDICT_PENDING,
                attempts=1,
            )
        )
        await session.commit()
    return row_id


async def deliver(
    worker: ReplyConsumer, row_id: uuid.UUID, seller_id: uuid.UUID, event_id: uuid.UUID | None = None
) -> None:
    await worker.process(
        {"event_id": str(event_id or uuid.uuid4()), "verdict_id": str(row_id), "seller_id": str(seller_id)}
    )


async def stored(database: Database, row_id: uuid.UUID) -> ReplyVerdictModel:
    async with database.session() as session:
        return (await session.scalars(select(ReplyVerdictModel).where(ReplyVerdictModel.id == row_id))).one()


async def test_the_class_is_stored_with_model_tokens_and_latency(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, [*BASE_FEED, REPLY], read_at=NOW)
    row_id = await request_reply(database, seller, REPLY)
    llm = FakeLLM()

    await deliver(reply_consumer(database, llm), row_id, seller)

    row = await stored(database, row_id)
    assert row.status == VERDICT_DONE and row.category == "refuses" and row.reason == "не хочет"
    assert (row.model, row.tokens_in, row.tokens_out, row.latency_ms) == ("qwen3-30b-a3b", 800, 30, 900)
    prompt = llm.calls[0][1]
    assert "1. не буду я ничего удалять" in prompt and "[продавец] Опишите, пожалуйста, проблему" in prompt


async def test_the_text_is_masked_and_the_notice_is_left_out(database: Database, seller: uuid.UUID) -> None:
    contact = event(REPLY_AT, "buyer", "мой номер +7 912 345-67-89, почта ivan@mail.ru")
    notice = event(REPLY_AT + 1, "buyer", NOTICE)
    await feed(database, seller, [*BASE_FEED, contact, notice], read_at=NOW)
    row_id = await request_reply(database, seller, contact, notice)
    llm = FakeLLM()

    await deliver(reply_consumer(database, llm), row_id, seller)

    sent = llm.calls[0][1]
    assert "912" not in sent and "mail.ru" not in sent and "<NUM>" in sent and "<EMAIL>" in sent
    assert "удалили это сообщение" not in sent


async def test_a_model_failure_is_recorded_and_not_raised(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, [*BASE_FEED, REPLY], read_at=NOW)
    row_id = await request_reply(database, seller, REPLY)
    llm = FakeLLM()
    llm.error = LLMError("модель ответила статусом 429")

    await deliver(reply_consumer(database, llm), row_id, seller)

    row = await stored(database, row_id)
    assert row.status == VERDICT_FAILED and row.category is None and "429" in (row.error or "")


async def test_an_unreadable_answer_is_stored_as_unclear(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, [*BASE_FEED, REPLY], read_at=NOW)
    row_id = await request_reply(database, seller, REPLY)

    await deliver(reply_consumer(database, FakeLLM("не знаю")), row_id, seller)

    assert (await stored(database, row_id)).category == "unclear"


async def test_a_slice_missing_from_the_mirror_fails_without_asking_the_model(
    database: Database, seller: uuid.UUID
) -> None:
    row_id = await request_reply(database, seller, REPLY)
    llm = FakeLLM()

    await deliver(reply_consumer(database, llm), row_id, seller)

    row = await stored(database, row_id)
    assert row.status == VERDICT_FAILED and "зеркал" in (row.error or "") and llm.calls == []


async def test_a_redelivered_event_does_not_ask_twice(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, [*BASE_FEED, REPLY], read_at=NOW)
    row_id = await request_reply(database, seller, REPLY)
    llm = FakeLLM()
    worker = reply_consumer(database, llm)
    event_id = uuid.uuid4()

    await deliver(worker, row_id, seller, event_id)
    await deliver(worker, row_id, seller, event_id)

    assert len(llm.calls) == 1


async def test_a_finished_row_is_not_overwritten_by_a_late_event(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, [*BASE_FEED, REPLY], read_at=NOW)
    row_id = await request_reply(database, seller, REPLY)
    await deliver(reply_consumer(database, FakeLLM()), row_id, seller)
    late = FakeLLM('{"category": "threat", "reason": "поздно"}')

    await deliver(reply_consumer(database, late), row_id, seller)

    assert (await stored(database, row_id)).category == "refuses" and late.calls == []


async def test_a_malformed_message_is_refused_for_good(database: Database) -> None:
    with pytest.raises(InvalidPayloadError):
        await reply_consumer(database, FakeLLM()).process({"event_id": "nope"})
    with pytest.raises(InvalidPayloadError):
        await reply_consumer(database, FakeLLM()).process({})


async def test_an_unknown_row_is_skipped(database: Database) -> None:
    llm = FakeLLM()

    await deliver(reply_consumer(database, llm), uuid.uuid4(), uuid.uuid4())

    assert llm.calls == []
