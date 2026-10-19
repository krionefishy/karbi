# ruff: noqa: F811 — фикстуры `database` и `seller` общие с тестами процессора и подставляются по имени.
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.modules.wb_support_bot.domain import (
    MANUAL_ALLOW,
    MANUAL_DENY,
    MODE_ENFORCE,
    MODE_OFF,
    MODE_SHADOW,
    SentMessage,
    may_send,
    parse_verdict,
    tail_fingerprint,
    tail_message,
    user_message,
)
from backend.modules.wb_support_bot.infrastructure.postgres import (
    VERDICT_DONE,
    VERDICT_FAILED,
    VERDICT_PENDING,
    SupportBotRepository,
    VerdictModel,
)
from backend.shared.llm import LLMError, LLMResult
from backend.storage.pg import Database
from backend.tests.wb_support_bot.test_support_bot_processor import (  # noqa: F401  (фикстуры и помощники общие)
    BASE_FEED,
    NOW,
    PROMPT_AT,
    database,
    event,
    feed,
    seller,
)
from backend.workers.wb_support_bot.classify_consumer import ClassifyConsumer, InvalidPayloadError


def test_the_tail_is_the_last_human_message_after_the_buyer_replied() -> None:
    events = [*BASE_FEED, event(90, "specialist", "Закрываем обращение")]

    tail = tail_message(events, [])

    assert tail is not None and tail.event_id == "specialist-90"


def test_bot_messages_do_not_change_the_tail() -> None:
    events = [*BASE_FEED, event(300, "bot", "Напоминание бота")]
    sent = [SentMessage(PROMPT_AT + timedelta(minutes=300), "Напоминание бота")]

    tail = tail_message(events, sent)

    assert tail is not None and tail.event_id == "specialist-60"


def test_there_is_no_tail_until_the_buyer_replies_or_a_human_writes() -> None:
    no_reply = BASE_FEED[:2]
    only_third_party_before_reply = [BASE_FEED[0], BASE_FEED[1], BASE_FEED[2]]

    assert tail_message(no_reply, []) is None
    assert tail_message(only_third_party_before_reply, []) is None


def test_the_fingerprint_follows_the_message_and_its_text() -> None:
    first = event(60, "specialist", "Опишите проблему")
    edited = event(60, "specialist", "Опишите проблему подробнее")
    same = event(60, "specialist", "  Опишите проблему ")

    assert tail_fingerprint(first) == tail_fingerprint(same)
    assert tail_fingerprint(first) != tail_fingerprint(edited)
    assert tail_fingerprint(first) != tail_fingerprint(event(61, "specialist", "Опишите проблему"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"verdict": "open", "reason": "просит выбрать"}', ("open", "просит выбрать")),
        ('Вот ответ: {"verdict": "CLOSING", "reason": "одобрено"} спасибо', ("closing", "одобрено")),
        ('{"verdict": "waiting_wb", "reason": "старый класс"}', ("unclear", "старый класс")),
        ('{"verdict": "open"}', ("open", "")),
        ("просто текст", ("unclear", "ответ модели без JSON")),
        ("{not json}", ("unclear", "ответ модели не разобран")),
        ('["open"]', ("unclear", "ответ модели без JSON")),
    ],
)
def test_anything_unreadable_becomes_unclear(raw: str, expected: tuple[str, str]) -> None:
    assert parse_verdict(raw) == expected


def test_a_long_reason_is_cut() -> None:
    _, reason = parse_verdict('{"verdict": "open", "reason": "' + "я" * 500 + '"}')
    assert len(reason) == 200


@pytest.mark.parametrize("mode", [MODE_OFF, MODE_SHADOW])
@pytest.mark.parametrize("verdict", [None, "open", "closing", "unclear"])
@pytest.mark.parametrize("manual", [None, MANUAL_ALLOW, MANUAL_DENY])
def test_outside_enforce_the_verdict_never_blocks(mode: str, verdict: str | None, manual: str | None) -> None:
    assert may_send(mode, verdict, manual) is True


@pytest.mark.parametrize(
    ("verdict", "manual", "allowed"),
    [
        ("open", None, True),
        ("closing", None, False),
        ("unclear", None, False),
        (None, None, False),
        ("closing", MANUAL_ALLOW, True),
        (None, MANUAL_ALLOW, True),
        ("open", MANUAL_DENY, False),
    ],
)
def test_enforce_writes_only_to_a_dialog_that_still_awaits_the_buyer(
    verdict: str | None, manual: str | None, allowed: bool
) -> None:
    assert may_send(MODE_ENFORCE, verdict, manual) is allowed


def test_the_model_gets_only_the_message_text() -> None:
    assert user_message("  Заявка одобрена  ") == "Сообщение продавца:\nЗаявка одобрена"
    assert user_message("   ") == "Сообщение продавца:\n(без текста)"


class FakeLLM:
    def __init__(self, answer: str = '{"verdict": "open", "reason": "ждёт выбора"}') -> None:
        self.answer = answer
        self.error: Exception | None = None
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str) -> LLMResult:
        self.calls.append((system, user))
        if self.error is not None:
            raise self.error
        return LLMResult(self.answer, "qwen3-30b-a3b", 70, 14, 420)


async def request(database: Database, seller_id: uuid.UUID, *, tail_event_id: str) -> uuid.UUID:
    verdict_id = uuid.uuid4()
    async with database.session() as session:
        SupportBotRepository(session).add_verdict(
            VerdictModel(
                id=verdict_id,
                seller_id=seller_id,
                chat_id="1:chat-a",
                tail_event_id=tail_event_id,
                tail_fingerprint="f" * 40,
                prompt_version="v2",
                status=VERDICT_PENDING,
                attempts=1,
            )
        )
        await session.commit()
    return verdict_id


async def stored(database: Database, verdict_id: uuid.UUID) -> VerdictModel:
    async with database.session() as session:
        return (await session.scalars(select(VerdictModel).where(VerdictModel.id == verdict_id))).one()


def consumer(database: Database, llm: FakeLLM) -> ClassifyConsumer:
    return ClassifyConsumer(database, "unused:9092", "test-group", llm)  # type: ignore[arg-type]


async def deliver(consumer: ClassifyConsumer, verdict_id: uuid.UUID, seller_id: uuid.UUID, event_id: uuid.UUID) -> None:
    await consumer.process({"event_id": str(event_id), "verdict_id": str(verdict_id), "seller_id": str(seller_id)})


async def test_the_answer_is_stored_with_model_tokens_and_latency(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, BASE_FEED, read_at=NOW)
    verdict_id = await request(database, seller, tail_event_id="specialist-60")
    llm = FakeLLM()

    await deliver(consumer(database, llm), verdict_id, seller, uuid.uuid4())

    row = await stored(database, verdict_id)
    assert row.status == VERDICT_DONE and row.verdict == "open" and row.reason == "ждёт выбора"
    assert (row.model, row.tokens_in, row.tokens_out, row.latency_ms) == ("qwen3-30b-a3b", 70, 14, 420)
    assert row.error is None and row.completed_at is not None
    assert llm.calls[0][1] == "Сообщение продавца:\nОпишите, пожалуйста, проблему"


async def test_the_text_is_masked_before_it_leaves(database: Database, seller: uuid.UUID) -> None:
    feed_with_contact = [*BASE_FEED[:3], event(60, "specialist", "Позвоните +7 912 345-67-89 или ivan@mail.ru")]
    await feed(database, seller, feed_with_contact, read_at=NOW)
    verdict_id = await request(database, seller, tail_event_id="specialist-60")
    llm = FakeLLM()

    await deliver(consumer(database, llm), verdict_id, seller, uuid.uuid4())

    sent = llm.calls[0][1]
    assert "912" not in sent and "mail.ru" not in sent and "<NUM>" in sent and "<EMAIL>" in sent


async def test_a_model_failure_is_recorded_and_not_raised(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, BASE_FEED, read_at=NOW)
    verdict_id = await request(database, seller, tail_event_id="specialist-60")
    llm = FakeLLM()
    llm.error = LLMError("модель ответила статусом 429")

    await deliver(consumer(database, llm), verdict_id, seller, uuid.uuid4())

    row = await stored(database, verdict_id)
    assert row.status == VERDICT_FAILED and row.verdict is None and "429" in (row.error or "")
    assert row.completed_at is not None


async def test_an_unreadable_answer_is_stored_as_unclear(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, BASE_FEED, read_at=NOW)
    verdict_id = await request(database, seller, tail_event_id="specialist-60")

    await deliver(consumer(database, FakeLLM("не знаю")), verdict_id, seller, uuid.uuid4())

    row = await stored(database, verdict_id)
    assert row.status == VERDICT_DONE and row.verdict == "unclear"


async def test_a_message_missing_from_the_mirror_fails_without_asking_the_model(
    database: Database, seller: uuid.UUID
) -> None:
    verdict_id = await request(database, seller, tail_event_id="gone-1")
    llm = FakeLLM()

    await deliver(consumer(database, llm), verdict_id, seller, uuid.uuid4())

    row = await stored(database, verdict_id)
    assert row.status == VERDICT_FAILED and "зеркал" in (row.error or "") and llm.calls == []


async def test_a_redelivered_event_does_not_ask_twice(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, BASE_FEED, read_at=NOW)
    verdict_id = await request(database, seller, tail_event_id="specialist-60")
    llm = FakeLLM()
    worker = consumer(database, llm)
    event_id = uuid.uuid4()

    await deliver(worker, verdict_id, seller, event_id)
    await deliver(worker, verdict_id, seller, event_id)

    assert len(llm.calls) == 1


async def test_a_finished_verdict_is_not_overwritten_by_a_late_event(database: Database, seller: uuid.UUID) -> None:
    await feed(database, seller, BASE_FEED, read_at=NOW)
    verdict_id = await request(database, seller, tail_event_id="specialist-60")
    await deliver(consumer(database, FakeLLM()), verdict_id, seller, uuid.uuid4())
    late = FakeLLM('{"verdict": "closing", "reason": "поздно"}')

    await deliver(consumer(database, late), verdict_id, seller, uuid.uuid4())

    assert (await stored(database, verdict_id)).verdict == "open" and late.calls == []


async def test_a_malformed_message_is_refused_for_good(database: Database) -> None:
    with pytest.raises(InvalidPayloadError):
        await consumer(database, FakeLLM()).process({"event_id": "nope"})
    with pytest.raises(InvalidPayloadError):
        await consumer(database, FakeLLM()).process({})


async def test_an_unknown_verdict_row_is_skipped(database: Database) -> None:
    llm = FakeLLM()

    await deliver(consumer(database, llm), uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    assert llm.calls == []
