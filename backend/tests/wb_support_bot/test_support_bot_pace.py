import random
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from backend.modules.wb_core.application import ChatMirror
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.postgres.models import SellerModel
from backend.modules.wb_core.infrastructure.wb.client import WBPermanentError
from backend.modules.wb_support_bot.application import (
    BotPolicy,
    BotRequestError,
    SupportBotProcessor,
    SupportBotService,
)
from backend.modules.wb_support_bot.domain import Pace, validate_pace
from backend.modules.wb_support_bot.infrastructure.postgres import DialogModel, SendModel, SupportBotRepository
from backend.shared.settings import load_settings
from backend.storage.pg import Database
from backend.tests.wb_support_bot.test_support_bot_processor import (
    BASE_FEED,
    NOW,
    PROMPT_AT,
    TEXT,
    FakeSender,
    dialog,
    event,
    feed,
    go_live,
    no_sleep,
)
from backend.workers.wb_support_bot.worker import SupportBotWorker

SETTINGS = load_settings("backend/shared/settings/config.test.yaml")
NM_ID = 1304195061


@pytest_asyncio.fixture
async def database() -> AsyncIterator[Database]:
    database = Database()
    await database.connect(SETTINGS.database.url, pool_size=2, max_overflow=0)
    try:
        yield database
    finally:
        await database.disconnect()


@pytest_asyncio.fixture
async def seller(database: Database) -> AsyncIterator[uuid.UUID]:
    async with database.session() as session:
        model = SellerModel(name="ИП Шаг", catalog_sync_status="success", egress_status="verified")
        session.add(model)
        await session.flush()
        seller_id = model.id
        repository = SupportBotRepository(session)
        await repository.track(seller_id)
        await repository.add_template(seller_id, TEXT)
        await session.commit()
    try:
        yield seller_id
    finally:
        async with database.session() as session:
            await SupportBotRepository(session).purge_seller(seller_id)
            await session.execute(delete(SellerModel).where(SellerModel.id == seller_id))
            await session.commit()


async def set_pace(database: Database, seller_id: uuid.UUID, *values: int | None) -> None:
    async with database.session() as session:
        await SupportBotRepository(session).set_pace(seller_id, *values)
        await session.commit()


async def run(database: Database, seller_id: uuid.UUID, sender: FakeSender, *, now: datetime = NOW):
    async with database.session() as session:
        processor = SupportBotProcessor(
            SupportBotRepository(session),
            ChatMirror(session),
            sender,
            BotPolicy(),
            rng=random.Random(1),
            sleep=no_sleep,
        )
        return await processor.process_seller(seller_id, now=now)


def minutes(moment: datetime | None, since: datetime = NOW) -> float:
    assert moment is not None
    return (moment - since).total_seconds() / 60


# --- шаг кабинета ---


async def test_a_cabinet_pace_replaces_the_shared_one(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await set_pace(database, seller, 120, 1, 3)
    await feed(database, seller, BASE_FEED, read_at=NOW)

    report = await run(database, seller, FakeSender())

    assert report.sent == 1
    assert 121 <= minutes((await dialog(database, seller)).next_send_at) <= 123


async def test_values_not_set_fall_back_to_the_shared_ones(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await set_pace(database, seller, None, None, None)
    await feed(database, seller, BASE_FEED, read_at=NOW)

    await run(database, seller, FakeSender())

    assert 61 <= minutes((await dialog(database, seller)).next_send_at) <= 70


async def test_only_the_interval_set_keeps_the_shared_jitter(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await set_pace(database, seller, 30, None, None)
    await feed(database, seller, BASE_FEED, read_at=NOW)

    await run(database, seller, FakeSender())

    assert 31 <= minutes((await dialog(database, seller)).next_send_at) <= 40


@pytest.mark.parametrize(
    ("values", "text"),
    [
        ((9, 1, 3), "от 10 до 1440"),
        ((1441, 1, 3), "от 10 до 1440"),
        ((60, -1, 3), "отрицательной"),
        ((60, 5, 4), "меньше"),
        ((60, 1, 241), "больше 240"),
    ],
)
def test_pace_limits(values: tuple[int, int, int], text: str) -> None:
    with pytest.raises(ValueError, match=text):
        validate_pace(*values)


def test_pace_limits_accept_the_edges() -> None:
    validate_pace(10, 0, 0)
    validate_pace(1440, 240, 240)


# --- пересчёт при сохранении ---


def service(session) -> SupportBotService:  # type: ignore[no-untyped-def]
    return SupportBotService(session, SellerRepository(session), SupportBotRepository(session))


async def save(database: Database, seller_id: uuid.UUID, *values: int | None, now: datetime, seed: int = 1) -> int:
    async with database.session() as session:
        return await service(session).set_pace(seller_id, *values, now=now, rng=random.Random(seed))


async def test_saving_recalculates_running_dialogs_from_the_last_send(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    await run(database, seller, FakeSender())

    assert await save(database, seller, 120, 1, 3, now=NOW + timedelta(minutes=10)) == 1

    assert 121 <= minutes((await dialog(database, seller)).next_send_at) <= 123


async def test_saving_the_same_values_again_moves_nothing(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    await run(database, seller, FakeSender())
    await save(database, seller, 120, 1, 3, now=NOW + timedelta(minutes=10))
    before = (await dialog(database, seller)).next_send_at

    assert await save(database, seller, 120, 1, 3, now=NOW + timedelta(minutes=20), seed=2) == 0

    assert (await dialog(database, seller)).next_send_at == before


async def test_resetting_goes_back_to_the_shared_pace(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    await run(database, seller, FakeSender())
    await save(database, seller, 120, 1, 3, now=NOW + timedelta(minutes=10))

    await save(database, seller, None, None, None, now=NOW + timedelta(minutes=11))

    assert 61 <= minutes((await dialog(database, seller)).next_send_at) <= 70


async def test_a_new_slot_beyond_the_window_moves_to_the_next_opening(database: Database, seller: uuid.UUID) -> None:
    await go_live(database, seller)
    await feed(database, seller, BASE_FEED, read_at=NOW)
    await run(database, seller, FakeSender())

    await save(database, seller, 600, 1, 3, now=NOW + timedelta(minutes=10))

    moved = (await dialog(database, seller)).next_send_at
    assert moved is not None
    opening = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)  # 09:00 МСК следующего дня
    assert opening <= moved <= opening + timedelta(minutes=3)


async def add_dialog(
    database: Database,
    seller_id: uuid.UUID,
    index: int,
    *,
    last_sent_at: datetime | None,
    next_send_at: datetime | None,
) -> None:
    async with database.session() as session:
        session.add(
            DialogModel(
                seller_id=seller_id,
                chat_id=f"1:bulk-{index}",
                prompt_at=PROMPT_AT,
                state="in_campaign",
                campaigns_count=1,
                sent_count=1 if last_sent_at else 0,
                no_contact=False,
                attention=False,
                excluded=False,
                closed=False,
                last_sent_at=last_sent_at,
                next_send_at=next_send_at,
            )
        )
        await session.commit()


async def test_overdue_dialogs_are_spread_over_minutes_and_idle_ones_are_left_alone(
    database: Database, seller: uuid.UUID
) -> None:
    for index in range(30):
        await add_dialog(
            database, seller, index, last_sent_at=NOW - timedelta(hours=6), next_send_at=NOW - timedelta(hours=5)
        )
    await add_dialog(database, seller, 100, last_sent_at=None, next_send_at=NOW)
    await add_dialog(database, seller, 101, last_sent_at=NOW - timedelta(hours=6), next_send_at=None)

    assert await save(database, seller, 120, 1, 3, now=NOW) == 30

    async with database.session() as session:
        rows = {
            row.chat_id: row
            for row in await session.scalars(select(DialogModel).where(DialogModel.seller_id == seller))
        }
    spread = [minutes(rows[f"1:bulk-{index}"].next_send_at) for index in range(30)]
    assert all(0 <= value <= 10 for value in spread) and len({round(value, 1) for value in spread}) > 20
    assert rows["1:bulk-100"].next_send_at == NOW and rows["1:bulk-101"].next_send_at is None


async def test_saving_refuses_a_bad_pace_and_keeps_the_old_one(database: Database, seller: uuid.UUID) -> None:
    await set_pace(database, seller, 90, 2, 5)

    with pytest.raises(BotRequestError, match="от 10 до 1440"):
        await save(database, seller, 5, 1, 3, now=NOW)
    with pytest.raises(BotRequestError, match="меньше"):
        await save(database, seller, 60, 9, 3, now=NOW)

    async with database.session() as session:
        tracked = await SupportBotRepository(session).tracked(seller)
    assert tracked is not None and (tracked.interval_minutes, tracked.jitter_min_minutes) == (90, 2)


async def test_a_partly_empty_pace_is_checked_against_the_shared_values(database: Database, seller: uuid.UUID) -> None:
    # Общая добавка 1–10: «до» = 0 при общем «от» = 1 не проходит.
    with pytest.raises(BotRequestError, match="меньше"):
        await save(database, seller, 60, None, 0, now=NOW)
    assert Pace().jitter_min == timedelta(minutes=1)


# --- лёгкий цикл отправки ---


class Clock:
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment


async def prepare(
    database: Database, seller_id: uuid.UUID, *, live: bool = True
) -> tuple[SupportBotWorker, FakeSender, Clock]:
    """Полный проход в остановленном режиме запускает кампанию (отправка пора); затем режим по `live`."""
    await feed(database, seller_id, BASE_FEED, read_at=NOW)
    sender = FakeSender()
    clock = Clock(NOW)
    worker = SupportBotWorker(database, sender, SETTINGS, now=clock)
    await worker.tick()
    assert sender.calls == []
    if live:
        await go_live(database, seller_id)
    clock.moment = NOW + timedelta(minutes=1)
    return worker, sender, clock


async def test_the_light_cycle_sends_what_is_ready(database: Database, seller: uuid.UUID) -> None:
    worker, sender, _ = await prepare(database, seller)

    assert await worker.send_tick() == 1

    assert sender.calls == [(str(seller), "1:sign", TEXT)]
    state = await dialog(database, seller)
    assert state.sent_count == 1 and 61 <= minutes(state.next_send_at, NOW + timedelta(minutes=1)) <= 70


async def test_the_light_cycle_uses_the_cabinet_pace(database: Database, seller: uuid.UUID) -> None:
    await set_pace(database, seller, 120, 1, 3)
    worker, _, _ = await prepare(database, seller)

    await worker.send_tick()

    assert 121 <= minutes((await dialog(database, seller)).next_send_at, NOW + timedelta(minutes=1)) <= 123


async def test_the_light_cycle_leaves_stopped_cabinets_alone(database: Database, seller: uuid.UUID) -> None:
    worker, sender, _ = await prepare(database, seller, live=False)

    assert await worker.send_tick() == 0
    assert sender.calls == []


async def test_the_light_cycle_waits_for_a_full_pass_after_new_events(database: Database, seller: uuid.UUID) -> None:
    worker, sender, clock = await prepare(database, seller)
    await feed(database, seller, [event(120, "buyer", "Спасибо, всё решили")], read_at=NOW + timedelta(seconds=30))

    assert await worker.send_tick() == 0
    assert sender.calls == []

    await worker.tick()  # полный проход видит ответ покупателя и снимает рассылку
    clock.moment += timedelta(minutes=11)
    assert await worker.send_tick() == 0
    assert sender.calls == []


async def test_the_light_cycle_resumes_after_the_next_full_pass(database: Database, seller: uuid.UUID) -> None:
    worker, sender, clock = await prepare(database, seller, live=False)
    # Новое событие не от покупателя: пока полный проход его не разобрал, лёгкий цикл молчит.
    await feed(
        database, seller, [event(61, "specialist", "Уточните номер заказа")], read_at=NOW + timedelta(seconds=30)
    )
    await go_live(database, seller)
    assert await worker.send_tick() == 0

    clock.moment = NOW + timedelta(minutes=11)
    await worker.tick()  # режим live: полный проход сам отправляет готовое
    assert len(sender.calls) == 1
    # Курсор запомнен: лёгкий цикл снова допущен (дублей нет, готовых диалогов уже нет).
    assert worker._projected[seller] is not None
    assert await worker.send_tick() == 0
    assert len(sender.calls) == 1


async def test_the_light_cycle_does_not_send_outside_the_window(database: Database, seller: uuid.UUID) -> None:
    worker, sender, clock = await prepare(database, seller)
    clock.moment = PROMPT_AT + timedelta(hours=17)

    assert await worker.send_tick() == 0
    assert sender.calls == []


async def test_the_light_cycle_obeys_the_global_switch(database: Database, seller: uuid.UUID) -> None:
    worker, sender, _ = await prepare(database, seller)
    async with database.session() as session:
        await SupportBotRepository(session).set_paused(True)
        await session.commit()
    try:
        assert await worker.send_tick() == 0
    finally:
        async with database.session() as session:
            await SupportBotRepository(session).set_paused(False)
            await session.commit()
    assert sender.calls == []


async def test_the_light_cycle_skips_a_cabinet_with_a_stop_reason(database: Database, seller: uuid.UUID) -> None:
    worker, sender, _ = await prepare(database, seller)
    async with database.session() as session:
        await SupportBotRepository(session).set_paused_reason(seller, "Ключ без доступа")
        await session.commit()

    assert await worker.send_tick() == 0
    assert sender.calls == []


async def test_the_light_cycle_holds_dialogs_of_a_disabled_product(database: Database, seller: uuid.UUID) -> None:
    worker, sender, _ = await prepare(database, seller)
    async with database.session() as session:
        await SupportBotRepository(session).set_product_enabled(seller, NM_ID, False, now=NOW)
        await session.commit()

    assert await worker.send_tick() == 0
    assert sender.calls == []


async def test_the_light_cycle_does_not_duplicate_a_send_with_the_full_pass(
    database: Database, seller: uuid.UUID
) -> None:
    worker, sender, clock = await prepare(database, seller)

    assert await worker.send_tick() == 1
    clock.moment += timedelta(minutes=1)
    assert await worker.send_tick() == 0
    clock.moment = NOW + timedelta(minutes=11)
    await worker.tick()

    assert len(sender.calls) == 1
    async with database.session() as session:
        journal = list(await session.scalars(select(SendModel).where(SendModel.seller_id == seller)))
    assert len(journal) == 1


async def test_a_cabinet_in_work_is_skipped_by_the_light_cycle(database: Database, seller: uuid.UUID) -> None:
    worker, sender, _ = await prepare(database, seller)

    async with worker._lock(seller):
        assert await worker.send_tick() == 0

    assert sender.calls == []
    assert await worker.send_tick() == 1


async def test_a_rejection_in_the_light_cycle_backs_off_like_in_the_full_pass(
    database: Database, seller: uuid.UUID
) -> None:
    worker, sender, clock = await prepare(database, seller)
    sender.error = WBPermanentError("чат закрыт")

    assert await worker.send_tick() == 0

    state = await dialog(database, seller)
    assert state.sent_count == 0 and minutes(state.next_send_at, clock.moment) == pytest.approx(30, abs=0.1)


async def test_an_access_error_in_the_light_cycle_stops_the_cabinet(database: Database, seller: uuid.UUID) -> None:
    worker, sender, _ = await prepare(database, seller)
    sender.error = WBPermanentError("Нет доступа к чатам")

    await worker.send_tick()

    async with database.session() as session:
        tracked = await SupportBotRepository(session).tracked(seller)
    assert tracked is not None and tracked.paused_reason == "Нет доступа к чатам"
