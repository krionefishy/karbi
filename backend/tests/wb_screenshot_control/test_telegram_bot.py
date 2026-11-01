"""Бот приёма скриншотов в Telegram на фейковом порте файлов: апдейт → строки в базе и ответы в outbox.

Релея в этом репозитории нет: файлы отдаёт `FakeFiles`, ответы проверяются по событиям
`message.requested`, которые модуль положил в outbox. Сотрудники и доступ — настоящие, из стенда.
"""

import uuid
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy import update as sa_update

from backend.modules.notifications.application import BotRegistry
from backend.modules.notifications.application import templates as t
from backend.modules.notifications.domain import (
    MessageRequest,
    MessengerFileTooLargeError,
    MessengerPermanentError,
    MessengerTemporaryError,
    UpdateEvent,
)
from backend.modules.notifications.infrastructure.postgres import BotModel, NotificationRepository
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.modules.wb_core.infrastructure.postgres.models import InboxEventModel, OutboxEventModel
from backend.modules.wb_screenshot_control.application.events import ScreenshotVerdictReady
from backend.modules.wb_screenshot_control.application.telegram_bot import (
    TelegramBotService,
    classify,
    outcome_action,
    parse_outcome_action,
    update_inbox_id,
)
from backend.modules.wb_screenshot_control.application.telegram_link import hash_link_token
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import (
    SubmissionModel,
    TelegramLinkModel,
    TelegramLinkTokenModel,
)
from backend.modules.wb_screenshot_control.infrastructure.storage import StagingStore
from backend.tests.wb_screenshot_control.fakes import screen
from backend.tests.wb_screenshot_control.http_stand import MANAGER, Stand, open_stand, synthetic_png

BOT = "screens"
TG_MANAGER = 7001
TG_OTHER = 7002
REPLY = "ScreenshotTelegramReply"


class FakeFiles:
    """Порт скачивания: `file_ref` → байты или исключение; помнит, что спрашивали."""

    def __init__(self, files: dict[str, bytes | Exception] | None = None) -> None:
        self.files = files or {}
        self.asked: list[tuple[str, str, int]] = []

    async def fetch(self, bot_code: str, file_ref: str, *, max_bytes: int) -> bytes:
        self.asked.append((bot_code, file_ref, max_bytes))
        found = self.files.get(file_ref, MessengerPermanentError("file is gone"))
        if isinstance(found, Exception):
            raise found
        return found


@pytest_asyncio.fixture
async def bot_stand(tmp_path: Path) -> AsyncIterator[Stand]:
    roles = ("admin", "manager", "other", "outsider")
    async with open_stand(tmp_path, roles=roles, without_service=("outsider",)) as stand:
        async with stand.database.session() as session:
            await session.execute(delete(BotModel).where(BotModel.code == BOT))
            await BotRegistry(session, NotificationRepository(session)).register(
                code=BOT,
                title="Скриншоты",
                invite_link_template="https://t.me/screens_bot?start={token}",
                interactive=True,
            )
        try:
            yield stand
        finally:
            async with stand.database.session() as session:
                await session.execute(delete(OutboxEventModel).where(OutboxEventModel.event_type == REPLY))
                await session.execute(
                    delete(InboxEventModel).where(
                        InboxEventModel.event_type.in_(["TelegramUpdateReceived", "ScreenshotVerdictTelegram"])
                    )
                )
                await session.execute(delete(BotModel).where(BotModel.code == BOT))
                await session.commit()


def bot(stand: Stand, files: FakeFiles | None = None, **config: Any) -> TelegramBotService:
    settings = stand.application.settings.screenshot_control
    return TelegramBotService(
        stand.database,
        StagingStore(stand.staging, max_used_percent=99),
        files or FakeFiles(),
        replace(settings, **config),
        bucket="",
    )


_UPDATE = iter(range(1, 10**9))


def update(*, user_id: int | None = TG_MANAGER, **fields: Any) -> dict[str, Any]:
    update_id = fields.pop("update_id", None) or next(_UPDATE) + int(uuid.uuid4().int % 10**6) * 1000
    payload: dict[str, Any] = {
        "bot": BOT,
        "update_id": update_id,
        "kind": "message",
        "chat_id": user_id or 1,
        "chat_type": "private",
        "user_id": user_id,
        "username": "ivan",
        "first_name": "Иван",
        "message_ref": "4521",
        "reply_to_ref": "",
        "text": "",
        "caption": "",
        "media_group_id": "",
        "forwarded": False,
        "attachments": [],
        "callback": None,
        "event_id": str(uuid.uuid4()),
    }
    payload.update(fields)
    return payload


def photo(file_ref: str = "ph1", **fields: Any) -> dict[str, Any]:
    attachment = {"kind": "photo", "file_ref": file_ref, "size": 1000, "width": 1280, "height": 800}
    return update(attachments=[attachment], **fields)


def document(file_ref: str = "doc1", mime: str = "image/png", size: int | None = 1000, **fields: Any) -> dict:
    return update(attachments=[{"kind": "document", "file_ref": file_ref, "mime": mime, "size": size}], **fields)


def press(data: str, **fields: Any) -> dict[str, Any]:
    return update(
        kind="callback", callback={"id": f"cb-{uuid.uuid4().hex[:6]}", "data": data, "message_ref": "4600"}, **fields
    )


async def replies(stand: Stand) -> list[MessageRequest]:
    async with stand.database.session() as session:
        rows = await session.scalars(
            select(OutboxEventModel).where(OutboxEventModel.event_type == REPLY).order_by(OutboxEventModel.created_at)
        )
        return [MessageRequest.parse(row.payload) for row in rows]


async def templates(stand: Stand) -> list[str]:
    return [item.template for item in await replies(stand)]


async def clear_replies(stand: Stand) -> None:
    async with stand.database.session() as session:
        await session.execute(delete(OutboxEventModel).where(OutboxEventModel.event_type == REPLY))
        await session.commit()


async def token_for(
    stand: Stand, role: str, *, expires_in: timedelta = timedelta(minutes=15), used: bool = False
) -> str:
    token = uuid.uuid4().hex
    now = datetime.now(UTC)
    async with stand.database.session() as session:
        session.add(
            TelegramLinkTokenModel(
                user_id=stand.users[role],
                token_hash=hash_link_token(token),
                expires_at=now + expires_in,
                used_at=now if used else None,
            )
        )
        await session.commit()
    return token


async def link(stand: Stand, role: str = "manager", telegram_user_id: int = TG_MANAGER) -> None:
    async with stand.database.session() as session:
        session.add(
            TelegramLinkModel(
                user_id=stand.users[role], telegram_user_id=telegram_user_id, chat_id=telegram_user_id, username="ivan"
            )
        )
        await session.commit()


async def live_links(stand: Stand) -> list[TelegramLinkModel]:
    async with stand.database.session() as session:
        return list(await session.scalars(select(TelegramLinkModel).where(TelegramLinkModel.revoked_at.is_(None))))


async def submissions(stand: Stand) -> list[SubmissionModel]:
    async with stand.database.session() as session:
        return list(await session.scalars(select(SubmissionModel).order_by(SubmissionModel.received_at)))


# --- чистые функции ---


class TestPure:
    def test_the_outcome_action_fits_the_callback_limit_and_round_trips(self) -> None:
        submission_id = uuid.uuid4()
        for outcome in ("positive", "amended", "deleted"):
            action = outcome_action(submission_id, outcome)
            assert len(action.encode()) <= 64
            assert parse_outcome_action(action) == (submission_id, outcome)

    @pytest.mark.parametrize(
        "data",
        [
            "",
            "sc:o:",
            "sc:o:abc:p",
            "sc:o:AAAAAAAAAAAAAAAAAAAAAA:x",
            "xx:o:AAAAAAAAAAAAAAAAAAAAAA:p",
            "sc:o:!!!!!!!!!!!!!!!!!!!!!!:p",
        ],
    )
    def test_garbage_is_not_an_outcome(self, data: str) -> None:
        assert parse_outcome_action(data) is None

    @pytest.mark.parametrize(
        ("payload", "intent", "command", "argument"),
        [
            (update(text="/start abc"), "start", "start", "abc"),
            (update(text="/start"), "start", "start", ""),
            (update(text="/Summary@screens_bot"), "command", "summary", ""),
            (photo(), "files", "", ""),
            (photo(text="подпись"), "files", "", ""),
            (update(text="привет"), "no_file", "", ""),
            (press("sc:o:x:p"), "callback", "", ""),
        ],
    )
    def test_classify(self, payload: dict, intent: str, command: str, argument: str) -> None:
        found = classify(UpdateEvent.parse(payload))
        assert (found.intent, found.command, found.argument) == (intent, command, argument)

    def test_the_inbox_id_is_deterministic_per_bot_and_update(self) -> None:
        assert update_inbox_id("screens", 5) == update_inbox_id("screens", 5)
        assert update_inbox_id("screens", 5) != update_inbox_id("screens", 6)
        assert update_inbox_id("screens", 5) != update_inbox_id("other", 5)


# --- привязка ---


class TestLinking:
    async def test_a_live_token_links_this_telegram_and_is_spent(self, bot_stand: Stand) -> None:
        token = await token_for(bot_stand, "manager")

        await bot(bot_stand).handle(update(text=f"/start {token}"))

        [linked] = await live_links(bot_stand)
        assert (linked.user_id, linked.telegram_user_id, linked.chat_id) == (
            bot_stand.users["manager"],
            TG_MANAGER,
            TG_MANAGER,
        )
        [reply] = await replies(bot_stand)
        assert reply.template == t.SCREENS_LINKED
        assert reply.params["username"].endswith("-manager")
        async with bot_stand.database.session() as session:
            row = await session.scalar(
                select(TelegramLinkTokenModel).where(TelegramLinkTokenModel.token_hash == hash_link_token(token))
            )
        assert row is not None and row.used_at is not None

    @pytest.mark.parametrize(
        ("case", "expected"),
        [
            ("expired", t.SCREENS_LINK_INVALID),
            ("used", t.SCREENS_LINK_INVALID),
            ("unknown", t.SCREENS_LINK_INVALID),
            ("blocked", t.SCREENS_NO_ACCESS),
            ("without_service", t.SCREENS_NO_ACCESS),
            ("telegram_taken", t.SCREENS_LINK_TAKEN),
            ("staff_taken", t.SCREENS_LINK_TAKEN),
            ("group", t.SCREENS_PRIVATE_ONLY),
        ],
    )
    async def test_link_refusals(self, bot_stand: Stand, case: str, expected: str) -> None:
        role = "outsider" if case == "without_service" else "manager"
        token = await token_for(
            bot_stand,
            role,
            expires_in=timedelta(minutes=-1) if case == "expired" else timedelta(minutes=15),
            used=case == "used",
        )
        if case == "unknown":
            token = "not-a-real-token"
        if case == "blocked":
            async with bot_stand.database.session() as session:
                await UserRepository(session).set_active(bot_stand.users["manager"], False)
        if case == "telegram_taken":
            await link(bot_stand, "other", TG_MANAGER)
        if case == "staff_taken":
            await link(bot_stand, "manager", TG_OTHER)
        before = {item.id for item in await live_links(bot_stand)}
        fields: dict[str, Any] = {"chat_type": "group", "chat_id": -100} if case == "group" else {}

        await bot(bot_stand).handle(update(text=f"/start {token}", **fields))

        assert await templates(bot_stand) == [expected]
        assert {item.id for item in await live_links(bot_stand)} == before

    async def test_the_same_telegram_twice_says_already_linked(self, bot_stand: Stand) -> None:
        await link(bot_stand)
        token = await token_for(bot_stand, "manager")

        await bot(bot_stand).handle(update(text=f"/start {token}"))

        [reply] = await replies(bot_stand)
        assert (reply.template, reply.params.get("already")) == (t.SCREENS_LINKED, True)
        assert len(await live_links(bot_stand)) == 1

    @pytest.mark.parametrize("payload", [photo(), update(text="привет"), update(text="/help"), update(text="/start")])
    async def test_a_stranger_is_told_to_link_on_the_site(self, bot_stand: Stand, payload: dict) -> None:
        files = FakeFiles()

        await bot(bot_stand, files).handle(payload)

        assert await templates(bot_stand) == [t.SCREENS_NOT_LINKED]
        assert files.asked == []
        assert await submissions(bot_stand) == []


# --- файлы ---


class TestFiles:
    async def test_a_photo_becomes_a_telegram_submission_with_outcome_buttons(self, bot_stand: Stand) -> None:
        await link(bot_stand)
        files = FakeFiles({"ph1": screen(1, fmt="JPEG")})
        payload = photo(caption="полож", media_group_id="777", message_ref="4521")

        await bot(bot_stand, files).handle(payload)

        [row] = await submissions(bot_stand)
        assert (row.user_id, row.channel, row.client_submission_id) == (
            bot_stand.users["manager"],
            "telegram",
            f"tg:{payload['update_id']}:0",
        )
        assert (row.tg_chat_id, row.tg_message_id, row.tg_media_group_id, row.caption_raw) == (
            TG_MANAGER,
            4521,
            "777",
            "полож",
        )
        assert row.outcome is None
        assert row.client_meta["telegram_compressed"] is True
        assert files.asked == [(BOT, "ph1", bot_stand.application.settings.screenshot_control.max_upload_bytes)]
        [reply] = await replies(bot_stand)
        assert reply.template == t.SCREENS_RECEIVED
        assert reply.params["reply_to"] == "4521"
        actions = [button["action"] for button in reply.params["buttons"][0]]
        assert [parse_outcome_action(action) for action in actions] == [
            (row.id, "positive"),
            (row.id, "amended"),
            (row.id, "deleted"),
        ]
        assert reply.audience.chat_id == TG_MANAGER

    async def test_a_png_document_is_taken_as_is(self, bot_stand: Stand) -> None:
        await link(bot_stand)

        await bot(bot_stand, FakeFiles({"doc1": synthetic_png()})).handle(document())

        [row] = await submissions(bot_stand)
        assert row.client_meta["telegram_compressed"] is False

    @pytest.mark.parametrize(
        ("payload", "files", "reason", "downloaded"),
        [
            (document(mime="application/pdf"), {}, "unsupported_type", False),
            (document(size=16 * 1024 * 1024), {}, "file_too_large", False),
            (document(size=None), {"doc1": MessengerFileTooLargeError("big")}, "file_too_large", True),
            (document(), {"doc1": b"%PDF-1.7 not an image at all"}, "unsupported_type", True),
            (document(), {"doc1": synthetic_png(300, 200)}, "too_small", True),
            (document(), {"doc1": MessengerPermanentError("gone")}, "unavailable", True),
            (update(attachments=[{"kind": "sticker", "file_ref": "s1"}]), {}, "unsupported_type", False),
            (update(text="просто текст"), {}, "no_file", False),
        ],
    )
    async def test_what_is_not_a_screenshot_is_refused_politely(
        self, bot_stand: Stand, payload: dict, files: dict, reason: str, downloaded: bool
    ) -> None:
        await link(bot_stand)
        fake = FakeFiles(files)

        await bot(bot_stand, fake).handle(payload)

        [reply] = await replies(bot_stand)
        assert (reply.template, reply.params["reason"]) == (t.SCREENS_REJECTED_FILE, reason)
        assert bool(fake.asked) is downloaded
        assert await submissions(bot_stand) == []

    async def test_a_temporary_download_failure_is_retried_and_then_parked_with_an_answer(
        self, bot_stand: Stand
    ) -> None:
        await link(bot_stand)
        service = bot(bot_stand, FakeFiles({"ph1": MessengerTemporaryError("relay down")}))
        payload = photo()

        with pytest.raises(MessengerTemporaryError):
            await service.handle(payload)
        assert await replies(bot_stand) == []
        await service.park(payload, "MessengerTemporaryError: relay down")
        await service.handle(payload)

        [reply] = await replies(bot_stand)
        assert (reply.template, reply.params["reason"]) == (t.SCREENS_REJECTED_FILE, "temporary")

    async def test_a_repeated_update_creates_nothing_twice(self, bot_stand: Stand) -> None:
        await link(bot_stand)
        files = FakeFiles({"ph1": screen(2, fmt="JPEG")})
        service = bot(bot_stand, files)
        payload = photo()

        await service.handle(payload)
        await service.handle(payload)

        assert len(await submissions(bot_stand)) == 1
        assert len(await replies(bot_stand)) == 1
        assert len(files.asked) == 1

    async def test_a_replay_after_a_lost_inbox_answers_the_same_without_downloading(self, bot_stand: Stand) -> None:
        await link(bot_stand)
        files = FakeFiles({"ph1": screen(3, fmt="JPEG")})
        service = bot(bot_stand, files)
        payload = photo()
        await service.handle(payload)
        async with bot_stand.database.session() as session:
            await session.execute(
                delete(InboxEventModel).where(InboxEventModel.event_id == update_inbox_id(BOT, payload["update_id"]))
            )
            await session.commit()

        await service.handle(payload)

        assert len(await submissions(bot_stand)) == 1
        assert len(files.asked) == 1
        first, second = await replies(bot_stand)
        assert first.dedupe_key == second.dedupe_key  # уведомления не пошлют второе сообщение

    async def test_photos_in_a_row_are_independent_cases(self, bot_stand: Stand) -> None:
        await link(bot_stand)
        files = FakeFiles({f"ph{n}": screen(10 + n, fmt="JPEG") for n in range(3)})
        service = bot(bot_stand, files)

        for n in range(3):
            await service.handle(photo(f"ph{n}", media_group_id="album", message_ref=str(5000 + n)))

        rows = await submissions(bot_stand)
        assert len(rows) == 3
        received = await replies(bot_stand)
        assert [item.params["reply_to"] for item in received] == ["5000", "5001", "5002"]
        assert all(item.params["buttons"] for item in received)

    async def test_the_upload_budget_holds_in_telegram_too(self, bot_stand: Stand) -> None:
        await link(bot_stand)
        files = FakeFiles({"a": screen(20, fmt="JPEG"), "b": screen(21, fmt="JPEG")})
        service = bot(bot_stand, files, uploads_per_minute=1)

        await service.handle(photo("a"))
        await service.handle(photo("b"))

        assert len(await submissions(bot_stand)) == 1
        assert (await replies(bot_stand))[-1].params["reason"] == "rate_limited"
        assert [ref for _, ref, _ in files.asked] == ["a"]

    async def test_the_same_file_again_is_received_without_buttons_and_judged_a_duplicate(
        self, bot_stand: Stand
    ) -> None:
        await link(bot_stand)
        data = screen(30, fmt="JPEG")
        service = bot(bot_stand, FakeFiles({"a": data, "b": data}))
        await service.handle(photo("a"))
        first = (await submissions(bot_stand))[0]
        await bot_stand.decide(first.id, "accepted")

        await service.handle(photo("b"))

        reply = (await replies(bot_stand))[-1]
        assert reply.params["decided"] is True
        assert "buttons" not in reply.params


# --- доступ ---


class TestAccess:
    @pytest.mark.parametrize("case", ["blocked", "service_revoked"])
    async def test_without_access_nothing_is_taken(self, bot_stand: Stand, case: str) -> None:
        await link(bot_stand)
        async with bot_stand.database.session() as session:
            users = UserRepository(session)
            if case == "blocked":
                await users.set_active(bot_stand.users["manager"], False)
            else:
                await users.replace_services(bot_stand.users["manager"], set(), bot_stand.users["admin"])
        files = FakeFiles({"ph1": screen(4, fmt="JPEG")})

        await bot(bot_stand, files).handle(photo())
        await bot(bot_stand, files).handle(press("sc:o:AAAAAAAAAAAAAAAAAAAAAA:p"))

        found = await replies(bot_stand)
        assert [item.template for item in found] == [t.SCREENS_NO_ACCESS, t.SCREENS_CALLBACK]
        assert found[1].params == {"result": "no_access"}
        assert files.asked == []
        assert await submissions(bot_stand) == []

    async def test_a_group_chat_is_refused_even_for_a_linked_user(self, bot_stand: Stand) -> None:
        await link(bot_stand)

        await bot(bot_stand, FakeFiles({"ph1": screen(5)})).handle(photo(chat_type="supergroup", chat_id=-100500))

        assert await templates(bot_stand) == [t.SCREENS_PRIVATE_ONLY]

    async def test_another_bots_updates_are_not_ours(self, bot_stand: Stand) -> None:
        await link(bot_stand)

        await bot(bot_stand).handle({**photo(), "bot": "wb-returns"})

        assert await replies(bot_stand) == []


# --- кнопки исхода ---


async def uploaded(stand: Stand, seed: int) -> SubmissionModel:
    await bot(stand, FakeFiles({"ph": screen(seed, fmt="JPEG")})).handle(photo("ph"))
    await clear_replies(stand)
    return (await submissions(stand))[-1]


class TestOutcomeButtons:
    async def test_a_press_sets_the_outcome_from_the_button_and_edits_the_message(self, bot_stand: Stand) -> None:
        await link(bot_stand)
        row = await uploaded(bot_stand, 40)
        payload = press(outcome_action(row.id, "amended"))

        await bot(bot_stand).handle(payload)

        fresh = await bot_stand.submission(row.id)
        assert (fresh.outcome, fresh.outcome_source) == ("amended", "button")
        answer, edit = await replies(bot_stand)
        assert (answer.kind, answer.target_ref, answer.params) == (
            "callback_answer",
            payload["callback"]["id"],
            {"result": "saved"},
        )
        assert (edit.kind, edit.target_ref, edit.template) == ("edit", "4600", t.SCREENS_OUTCOME_SET)
        assert "buttons" not in edit.params

    async def test_after_the_verdict_the_outcome_is_locked(self, bot_stand: Stand) -> None:
        await link(bot_stand)
        row = await uploaded(bot_stand, 41)
        await bot_stand.decide(row.id, "accepted")
        await clear_replies(bot_stand)

        await bot(bot_stand).handle(press(outcome_action(row.id, "deleted")))

        [answer] = await replies(bot_stand)
        assert answer.params == {"result": "locked"}
        assert (await bot_stand.submission(row.id)).outcome is None

    @pytest.mark.parametrize("which", ["foreign", "garbage", "missing"])
    async def test_a_press_on_what_is_not_yours_changes_nothing(self, bot_stand: Stand, which: str) -> None:
        await link(bot_stand)
        await link(bot_stand, "other", TG_OTHER)
        row = await uploaded(bot_stand, 42)
        data = {
            "foreign": outcome_action(row.id, "positive"),
            "garbage": "sc:o:zz:q",
            "missing": outcome_action(uuid.uuid4(), "positive"),
        }[which]

        await bot(bot_stand).handle(press(data, user_id=TG_OTHER))

        [answer] = await replies(bot_stand)
        assert answer.params == {"result": "not_found"}
        assert (await bot_stand.submission(row.id)).outcome is None


# --- вердикт ---


def verdict_event(row: SubmissionModel, verdict: str, previous: str | None = None) -> dict[str, Any]:
    event = ScreenshotVerdictReady(
        submission_id=row.id,
        user_id=row.user_id,
        channel=row.channel,  # type: ignore[arg-type]
        verdict=verdict,  # type: ignore[arg-type]
        previous_verdict=previous,  # type: ignore[arg-type]
        actor="system",
        decided_at=datetime.now(UTC),
    )
    return {**event.payload(), "event_id": str(uuid.uuid4())}


class TestVerdicts:
    @pytest.mark.parametrize(
        ("verdict", "reasons", "reason_text"),
        [
            ("accepted", [], "Принят"),
            ("needs_review", [], "Принят, проверяется"),
            ("duplicate", [], "Этот скриншот уже загружен ранее"),
            ("rejected", [{"code": "photo_of_screen"}], "Нужен скриншот, а не фото экрана"),
        ],
    )
    async def test_the_manager_hears_a_short_phrase_in_reply(
        self, bot_stand: Stand, verdict: str, reasons: list, reason_text: str
    ) -> None:
        await link(bot_stand)
        row = await uploaded(bot_stand, 50)
        async with bot_stand.database.session() as session:
            await session.execute(
                sa_update(SubmissionModel).where(SubmissionModel.id == row.id).values(reasons=reasons)
            )
            await session.commit()

        await bot(bot_stand).verdict(verdict_event(row, verdict))

        [message] = await replies(bot_stand)
        assert (message.template, message.params["verdict"], message.params["reason_text"]) == (
            t.SCREENS_VERDICT,
            verdict,
            reason_text,
        )
        assert message.params["reply_to"] == "4521"
        assert message.audience.chat_id == TG_MANAGER
        rendered = t.render(message.template, message.params)
        assert "порог" not in rendered and "photo_of_screen" not in rendered

    async def test_a_repeat_or_the_same_verdict_again_says_nothing_new(self, bot_stand: Stand) -> None:
        await link(bot_stand)
        row = await uploaded(bot_stand, 51)
        service = bot(bot_stand)
        event = verdict_event(row, "accepted")

        await service.verdict(event)
        await service.verdict(event)
        await service.verdict(verdict_event(row, "accepted", previous="accepted"))

        assert len(await replies(bot_stand)) == 1

    async def test_after_unlinking_or_for_the_web_nothing_is_sent(self, bot_stand: Stand) -> None:
        await link(bot_stand)
        row = await uploaded(bot_stand, 52)
        web = await bot_stand.upload(await bot_stand.sign_in("manager"), seed=53)
        web_row = await bot_stand.submission(web.json()["id"])
        await clear_replies(bot_stand)
        service = bot(bot_stand)

        await service.verdict(verdict_event(web_row, "accepted"))
        await bot(bot_stand).handle(update(text="/unlink"))
        await clear_replies(bot_stand)
        await service.verdict(verdict_event(row, "rejected"))

        assert await replies(bot_stand) == []


# --- команды ---


class TestCommands:
    async def test_help_summary_and_last(self, bot_stand: Stand) -> None:
        await link(bot_stand)
        row = await uploaded(bot_stand, 60)
        await bot(bot_stand).handle(press(outcome_action(row.id, "positive")))
        await bot_stand.decide(row.id, "accepted")
        await clear_replies(bot_stand)
        service = bot(bot_stand)

        for command in ("/help", "/summary", "/last", "/whatever"):
            await service.handle(update(text=command))

        help_, summary, last, unknown = await replies(bot_stand)
        assert (help_.template, unknown.template) == (t.SCREENS_HELP, t.SCREENS_HELP)
        assert summary.template == t.SCREENS_SUMMARY
        assert (summary.params["positive"], summary.params["accepted_total"]) == (1, 1)
        assert last.template == t.SCREENS_LAST
        [item] = last.params["items"]
        assert (item["outcome_label"], item["status_text"]) == ("отзыв положительный", "Принят")

    async def test_unlink_revokes_and_the_next_photo_is_a_strangers(self, bot_stand: Stand) -> None:
        await link(bot_stand)
        files = FakeFiles({"ph1": screen(7)})
        service = bot(bot_stand, files)

        await service.handle(update(text="/unlink"))
        await service.handle(photo())

        assert await templates(bot_stand) == [t.SCREENS_UNLINKED, t.SCREENS_NOT_LINKED]
        assert await live_links(bot_stand) == []
        assert files.asked == []


# --- ручки привязки ---


class TestLinkHttp:
    async def test_a_link_token_is_a_one_time_deep_link_and_only_its_hash_is_stored(self, bot_stand: Stand) -> None:
        manager = await bot_stand.sign_in("manager")

        first = await bot_stand.client.post(f"{MANAGER}/telegram/link-token", headers=manager)
        second = await bot_stand.client.post(f"{MANAGER}/telegram/link-token", headers=manager)

        assert first.status_code == 200, first.text
        assert set(first.json()) == {"url", "expires_at"}
        url = second.json()["url"]
        assert url.startswith("https://t.me/screens_bot?start=")
        token = url.rsplit("=", 1)[1]
        assert len(token) == 32 and len(token) <= 64
        async with bot_stand.database.session() as session:
            rows = list(
                await session.scalars(
                    select(TelegramLinkTokenModel).where(TelegramLinkTokenModel.user_id == bot_stand.users["manager"])
                )
            )
        assert {row.token_hash for row in rows} >= {hash_link_token(token)}
        assert all(token not in row.token_hash for row in rows)
        live = [row for row in rows if row.expires_at > datetime.now(UTC)]
        assert [row.token_hash for row in live] == [hash_link_token(token)]  # прежняя погашена

        first_token = first.json()["url"].rsplit("=", 1)[1]
        await bot(bot_stand).handle(update(text=f"/start {first_token}"))
        await bot(bot_stand).handle(update(text=f"/start {token}"))
        assert await templates(bot_stand) == [t.SCREENS_LINK_INVALID, t.SCREENS_LINKED]

        status = await bot_stand.client.get(f"{MANAGER}/telegram", headers=manager)
        assert status.json()["linked"] is True and status.json()["available"] is True
        assert status.json()["username"] == "ivan"
        assert (await bot_stand.client.delete(f"{MANAGER}/telegram", headers=manager)).status_code == 204
        assert (await bot_stand.client.get(f"{MANAGER}/telegram", headers=manager)).json()["linked"] is False

    async def test_without_an_interactive_bot_the_link_is_unavailable(self, bot_stand: Stand) -> None:
        async with bot_stand.database.session() as session:
            await session.execute(sa_update(BotModel).where(BotModel.code == BOT).values(interactive=False))
            await session.commit()
        manager = await bot_stand.sign_in("manager")

        response = await bot_stand.client.post(f"{MANAGER}/telegram/link-token", headers=manager)

        assert (response.status_code, response.headers["X-Error-Code"]) == (503, "telegram_unavailable")
        status = await bot_stand.client.get(f"{MANAGER}/telegram", headers=manager)
        assert status.json()["available"] is False

    async def test_a_manager_without_the_service_gets_403(self, bot_stand: Stand) -> None:
        outsider = await bot_stand.sign_in("outsider")

        response = await bot_stand.client.post(f"{MANAGER}/telegram/link-token", headers=outsider)

        assert response.status_code == 403


async def test_no_tokens_leak_into_logs_or_outbox(bot_stand: Stand) -> None:
    token = await token_for(bot_stand, "manager")

    await bot(bot_stand).handle(update(text=f"/start {token}"))

    async with bot_stand.database.session() as session:
        payloads = [str(row.payload) for row in await session.scalars(select(OutboxEventModel))]
        assert not any(token in item for item in payloads)
        await session.execute(text("SELECT 1"))
