"""Сквозной путь на живых PostgreSQL и Redis: HTTP-приём (веб и расширение) → консьюмеры распознавания
и решения → вердикт → сводка менеджера, карточка и очереди админа.

Настоящие здесь `Extractor` (с нормализацией), `decide`, репозиторий кандидатов, outbox и inbox. Подменены
только сервис модели (respx отвечает по содержимому картинки, в Cloud.ru ничего не уходит), S3 (память) и
Kafka: события берутся из outbox и отдаются тем же `EventConsumer.process`, что в воркере. Картинки —
синтетика Pillow, реальных скриншотов в репозитории нет.
"""

import base64
import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
import respx

from backend.modules.wb_screenshot_control.application.events import EVENT_EXTRACTED, EVENT_SUBMITTED
from backend.modules.wb_screenshot_control.application.extractor import Extractor
from backend.modules.wb_screenshot_control.application.outcomes import expire_outcomes
from backend.modules.wb_screenshot_control.domain.prompt import ADDRESS_BAR_PROMPT_V1
from backend.modules.wb_screenshot_control.infrastructure.vision import address_bar_part, full_frame_part, open_frame
from backend.modules.wb_screenshot_control.infrastructure.wiring import ScreenshotControlServices, build_decider
from backend.shared.llm import LLMClient
from backend.shared.settings.config import VisionConfig
from backend.tests.wb_screenshot_control.fakes import screen, take
from backend.tests.wb_screenshot_control.http_stand import ADMIN, EXTENSION, MANAGER, Stand, open_stand
from backend.workers.wb_screenshot_control.consumers import EventConsumer

VLM_URL = "https://vlm.test/v1/chat/completions"
MSK = ZoneInfo("Europe/Moscow")
MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
CHAT = "ad70e889-d235-43bf-a634-b60a65bbce9b"
OTHER_CHAT = "0f1e2d3c-4b5a-4968-8776-655443322110"
THIRD_CHAT = "5b6c7d8e-9f01-4a23-8b45-6c7d8e9f0a1b"
REVIEW = "Размер маломерит, ткань тонкая и просвечивает, швы кривые, пришлось вернуть товар продавцу"
OTHER_REVIEW = "Курьер опоздал на два дня, коробка помята, но сама куртка целая и тёплая, спасибо"
QUALITY = {
    "blurred": False,
    "cropped_missing_panel": False,
    "photo_of_screen": False,
    "moire_or_glare": False,
    "looks_edited": False,
    "dark_overlay_or_popup": False,
}


def chat_url(chat_id: str) -> str:
    return f"https://seller.wildberries.ru/chat-with-clients?chatId={chat_id}&chatType=1"


def yesterday(hour: int, minute: int) -> datetime:
    day = datetime.now(MSK) - timedelta(days=1)
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def desktop(chat_id: str | None, text: str, moment: datetime, *, buyer: str = "Анна Петрова") -> dict[str, Any]:
    """Ответ модели по окну браузера (схема `ScreenshotAnswerV1`)."""
    return {
        "layout": "desktop_window",
        "is_wb_seller_chat": True,
        "url": {"value": chat_url(chat_id) if chat_id else None, "confidence": 0.9 if chat_id else 0.0},
        "chat_id": {"value": chat_id, "confidence": 0.9 if chat_id else 0.0},
        "review": {
            "present": True,
            "date_text": f"{moment.day} {MONTHS[moment.month - 1]}, {moment:%H:%M}",
            "day": moment.day,
            "month": moment.month,
            "year": None,
            "hour": moment.hour,
            "minute": moment.minute,
            "date_confidence": 0.95,
            "stars": 2,
            "text": text,
            "text_truncated": False,
            "text_confidence": 0.9,
            "article": "123456789",
            "product_name": "Платье",
        },
        "last_client_message": {
            "present": False,
            "text": None,
            "time_text": None,
            "hour": None,
            "minute": None,
            "date_text": None,
            "day": None,
            "month": None,
            "year": None,
            "confidence": 0.0,
        },
        "messages": [
            {"side": "seller", "text": "Здравствуйте! Что не понравилось?", "time_text": "10:05", "date_text": None}
        ],
        "buyer_name": {"value": buyer, "confidence": 0.9},
        "article": {"value": None, "confidence": 0.0},
        "quality": QUALITY,
        "notes": None,
    }


def mobile(messages: Sequence[tuple[str, str, str]], *, buyer: str = "Мария Иванова") -> dict[str, Any]:
    """Нативный скрин WB Partners: реплики (сторона, текст, время) вчерашнего дня."""
    side, text, time_text = next(item for item in reversed(messages) if item[0] == "client")
    hour, minute = (int(part) for part in time_text.split(":"))
    answer = desktop(None, text, yesterday(hour, minute), buyer=buyer)
    answer.update(
        layout="mobile_app",
        url={"value": None, "confidence": 0.0},
        review={**answer["review"], "present": False, "text": None, "date_text": None, "stars": None},
        last_client_message={
            "present": True,
            "text": text,
            "time_text": time_text,
            "hour": hour,
            "minute": minute,
            "date_text": "Вчера",
            "day": None,
            "month": None,
            "year": None,
            "confidence": 0.9,
        },
        messages=[{"side": s, "text": t, "time_text": tt, "date_text": "Вчера"} for s, t, tt in messages],
        article={"value": "Артикул: 987654321", "confidence": 0.7},
    )
    return answer


def _key(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Vlm:
    """Сервис модели: отвечает по содержимому присланной картинки (кадр или вырезка адресной строки)."""

    def __init__(self) -> None:
        self.answers: dict[str, list[Any]] = {}
        self.calls: list[str] = []

    def register(self, image: bytes, frame: Any, address: Any = None) -> bytes:
        opened = open_frame(image)
        self.answers[_key(full_frame_part(opened, 2000).data)] = [frame]
        if address is not None:
            self.answers[_key(address_bar_part(opened).data)] = [address]
        return image

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        url = body["messages"][1]["content"][1]["image_url"]["url"]
        key = _key(base64.b64decode(url.split(",", 1)[1]))
        kind = "address" if body["messages"][0]["content"] == ADDRESS_BAR_PROMPT_V1 else "frame"
        self.calls.append(kind)
        queue = self.answers.get(key)
        assert queue, f"модели прислали незнакомую картинку ({kind})"
        item = queue[0] if len(queue) == 1 else queue.pop(0)
        if isinstance(item, httpx.Response):
            return item
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [{"message": {"content": json.dumps(item, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 1100, "completion_tokens": 150},
            },
        )


@dataclass
class Clock:
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __call__(self) -> datetime:
        return self.now


@dataclass
class World:
    stand: Stand
    vlm: Vlm
    clock: Clock
    extract: EventConsumer
    decide: EventConsumer
    tokens: dict[str, str] = field(default_factory=dict)

    async def pump(self) -> None:
        """Как outbox-publisher + консьюмеры: пока есть события, раздать их; отложенные — когда наступит срок."""
        for _ in range(100):
            submitted = await take(self.stand.database, EVENT_SUBMITTED)
            extracted = await take(self.stand.database, EVENT_EXTRACTED)
            if not submitted and not extracted:
                return
            for row in submitted:
                not_before = row.payload.get("not_before")
                if not_before:
                    self.clock.now = max(self.clock.now, datetime.fromisoformat(not_before))
                await self.extract.process(json.dumps(row.payload).encode())
            for row in extracted:
                if row.next_attempt_at is not None:
                    self.clock.now = max(self.clock.now, row.next_attempt_at)
                await self.decide.process(json.dumps(row.payload).encode())
        raise AssertionError("конвейер не сошёлся за 100 раздач")

    async def web(self, role: str, image: bytes, outcome: str | None = "positive") -> dict[str, Any]:
        response = await self.stand.upload(await self.stand.sign_in(role), image, outcome=outcome)
        assert response.status_code == 201, response.text
        await self.pump()
        return await self.card(role, response.json()["id"])

    async def extension(self, role: str, image: bytes, url: str, outcome: str = "positive") -> dict[str, Any]:
        token = self.tokens.get(role) or await self._pair(role)
        response = await self.stand.client.post(
            f"{EXTENSION}/submissions",
            headers={"Authorization": f"Bearer {token}", "X-Extension-Version": "0.1.0"},
            data={
                "outcome": outcome,
                "client_submission_id": str(uuid.uuid4()),
                "client_url": url,
                "client_captured_at": datetime.now(UTC).isoformat(),
                "tz_offset_min": "-180",
            },
            files={"file": ("tab.png", image, "image/png")},
        )
        assert response.status_code == 201, response.text
        await self.pump()
        card = await self.stand.client.get(
            f"{EXTENSION}/submissions/{response.json()['id']}",
            headers={"Authorization": f"Bearer {token}", "X-Extension-Version": "0.1.0"},
        )
        return dict(card.json())

    async def _pair(self, role: str) -> str:
        headers = await self.stand.sign_in(role)
        code = (await self.stand.client.post(f"{MANAGER}/extension/pairing-code", headers=headers)).json()["code"]
        body = {"code": code, "install_id": f"install-{role}", "browser": "Chrome 130", "ext_version": "0.1.0"}
        token: str = (await self.stand.client.post(f"{EXTENSION}/pair", json=body)).json()["token"]
        self.tokens[role] = token
        return token

    async def card(self, role: str, submission_id: str) -> dict[str, Any]:
        headers = await self.stand.sign_in(role)
        response = await self.stand.client.get(f"{MANAGER}/submissions/{submission_id}", headers=headers)
        return dict(response.json())

    async def summary(self, role: str = "manager") -> dict[str, Any]:
        response = await self.stand.client.get(f"{MANAGER}/summary", headers=await self.stand.sign_in(role))
        return dict(response.json())

    async def admin(self, submission_id: str) -> dict[str, Any]:
        headers = await self.stand.sign_in("admin")
        return dict((await self.stand.client.get(f"{ADMIN}/submissions/{submission_id}", headers=headers)).json())

    async def queue(self, kind: str = "review") -> list[str]:
        headers = await self.stand.sign_in("admin")
        page = (await self.stand.client.get(f"{ADMIN}/queue", params={"kind": kind}, headers=headers)).json()
        return [item["id"] for item in page["items"]]


async def _world(tmp_path: Path, **overrides: Any) -> AsyncIterator[World]:
    async with open_stand(tmp_path, audit_percent=0.0, **overrides) as stand:
        vlm = Vlm()
        clock = Clock()
        services = ScreenshotControlServices(stand.database, stand.application.settings, blobs=stand.blobs)
        extractor = Extractor(
            LLMClient(VisionConfig(base_url="https://vlm.test/v1", api_key="secret")),
            timezone="Europe/Moscow",
            hmac_key="test-hmac-key",
            text_threshold=0.8,
            max_image_side=2000,
        )
        extraction = services.extraction(extractor)
        extraction.now = clock
        decision = services.decision(build_decider(stand.application.settings))
        decision.now = clock
        world = World(
            stand,
            vlm,
            clock,
            EventConsumer(topic="submitted", bootstrap_servers="", group_id="e2e", handler=extraction.handle),
            EventConsumer(topic="extracted", bootstrap_servers="", group_id="e2e", handler=decision.handle),
        )
        with respx.mock(assert_all_called=False) as mock:
            mock.post(VLM_URL).mock(side_effect=vlm)
            yield world


@pytest_asyncio.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    async for opened in _world(tmp_path):
        yield opened


def codes(card: dict[str, Any]) -> list[str]:
    return [item["code"] for item in card["reasons"]]


async def test_a_unique_screen_is_accepted_and_counted(world: World) -> None:
    image = world.vlm.register(
        screen(1), desktop(CHAT, REVIEW, yesterday(6, 39)), {"url": chat_url(CHAT), "chat_id": CHAT}
    )

    card = await world.web("manager", image)

    assert (card["status"], card["reason_code"]) == ("accepted", None)
    assert card["fields"]["chat_tail"] == CHAT[-6:] and card["fields"]["stars"] == 2
    assert world.vlm.calls == ["frame", "address"]
    detail = await world.admin(card["id"])
    assert detail["stage"] == "decided" and detail["auto_verdict"] == "accepted" and detail["reasons"] == []
    assert detail["fields"]["chat_id"] == CHAT and detail["agreement"]["chat_id"] == 1.0
    assert detail["extraction"]["tokens_in"] == 2200 and "Анна" not in json.dumps(detail, ensure_ascii=False)
    summary = await world.summary()
    assert summary["counts"]["accepted_total"] == summary["counts"]["positive"] == 1
    assert summary["preliminary"] is True and summary["mode"] == "shadow"


async def test_copies_of_one_case_are_duplicates(world: World) -> None:
    """Тот же файл другим менеджером — дубль при приёме; тот же кейс другим файлом — дубль по ключу кейса."""
    answer = desktop(CHAT, REVIEW, yesterday(6, 39))
    address = {"url": chat_url(CHAT), "chat_id": CHAT}
    image = world.vlm.register(screen(2), answer, address)
    original = await world.web("manager", image)
    assert original["status"] == "accepted"

    same_file = await world.web("other", image)
    assert (same_file["status"], same_file["reason_code"]) == ("duplicate", "already_uploaded")
    assert same_file["duplicate_received_at"] == original["received_at"]
    detail = await world.admin(same_file["id"])
    assert codes(detail) == ["exact_file", "cross_user_duplicate"] and detail["suspicion"] == "high"
    assert detail["duplicate_of"] == original["id"]
    assert world.vlm.calls == ["frame", "address"], "точную копию модель не читает"

    recoded = world.vlm.register(screen(2, fmt="JPEG", quality=80), answer, address)
    copy = await world.web("other", recoded)
    assert copy["status"] == "duplicate"
    detail = await world.admin(copy["id"])
    assert codes(detail)[:2] == ["case_key", "cross_user_duplicate"] and detail["duplicate_of"] == original["id"]
    assert set(await world.queue("duplicates")) >= {same_file["id"], copy["id"]}
    other = await world.summary("other")
    assert (other["duplicates"], other["counts"]["accepted_total"]) == (2, 0)


async def test_a_swapped_chat_id_goes_to_review(world: World) -> None:
    """Тот же текст и минута отзыва, но расширение прислало адрес другого чата."""
    moment = yesterday(6, 39)
    first = world.vlm.register(screen(3), desktop(CHAT, REVIEW, moment), {"url": chat_url(CHAT), "chat_id": CHAT})
    assert (await world.web("manager", first))["status"] == "accepted"

    spoof = world.vlm.register(screen(4), desktop(None, REVIEW, moment))
    card = await world.extension("other", spoof, chat_url(OTHER_CHAT))

    assert (card["status"], card["reason_code"]) == ("needs_review", "under_review")
    detail = await world.admin(card["id"])
    assert "same_text_time_other_chat" in codes(detail) and detail["suspicion"] == "high"
    assert detail["fields"]["chat_id"] == OTHER_CHAT and detail["channel"] == "extension"
    assert card["id"] in await world.queue("review")


async def test_a_swapped_chat_id_with_time_and_word_edits_goes_to_review(world: World) -> None:
    """Подмена chatId + сдвиг времени + замена слова другим менеджером: ни чат, ни минута не совпадают,
    кандидата находит только MinHash текста в `repository.candidates`."""
    first = world.vlm.register(
        screen(11), desktop(CHAT, REVIEW, yesterday(6, 39)), {"url": chat_url(CHAT), "chat_id": CHAT}
    )
    original = await world.web("manager", first)
    assert original["status"] == "accepted"

    edited = REVIEW.replace("кривые", "неровные")
    spoof = world.vlm.register(screen(12), desktop(None, edited, yesterday(10, 2)))
    card = await world.extension("other", spoof, chat_url(OTHER_CHAT))

    assert card["status"] == "needs_review"
    detail = await world.admin(card["id"])
    assert {"same_text_other_time", "cross_user_duplicate"} <= set(codes(detail)), codes(detail)
    assert detail["suspicion"] == "high"
    assert any(match["other_id"] == original["id"] for match in detail["matches"])


async def test_a_dom_edit_of_a_counted_case_goes_to_review(world: World) -> None:
    """Тот же чат, текст с заменой слова, другое время — повторно кейс не засчитывается."""
    first = world.vlm.register(screen(5), desktop(None, REVIEW, yesterday(6, 39)))
    assert (await world.extension("manager", first, chat_url(CHAT)))["status"] == "accepted"

    edited = REVIEW.replace("кривые", "неровные")
    second = world.vlm.register(screen(6), desktop(None, edited, yesterday(11, 20)))
    card = await world.extension("manager", second, chat_url(CHAT))

    assert card["status"] == "needs_review"
    detail = await world.admin(card["id"])
    assert codes(detail)[0] in ("same_chat_same_text_other_time", "same_chat_similar_text")


async def test_a_phone_screen_with_the_dialogue_continued_is_a_duplicate(world: World) -> None:
    opening = [
        ("client", "Здравствуйте, пришла куртка с порванным швом на рукаве", "21:05"),
        ("seller", "Добрый вечер! Пришлите, пожалуйста, фото", "21:10"),
        ("client", "Фото приложила, шов разошёлся сантиметров на пять", "21:15"),
    ]
    first = world.vlm.register(screen(7, width=600, height=1300), mobile(opening))
    assert (await world.web("manager", first))["status"] == "accepted"

    continued = [
        *opening,
        ("seller", "Оформите возврат, деньги вернём", "21:30"),
        ("client", "Спасибо, оформила возврат", "21:40"),
    ]
    second = world.vlm.register(screen(8, width=600, height=1300), mobile(continued))
    card = await world.web("manager", second)

    assert card["status"] == "duplicate"
    detail = await world.admin(card["id"])
    assert codes(detail) == ["messages_overlap"]
    assert world.vlm.calls == ["frame", "frame"], "у телефона адресной строки нет"


async def test_a_failing_model_is_retried_on_schedule_and_then_reviewed(world: World) -> None:
    image = world.vlm.register(screen(9), httpx.Response(500, json={"error": "down"}))
    started = world.clock.now

    card = await world.web("manager", image)

    assert (card["status"], card["reason_code"]) == ("needs_review", "under_review")
    detail = await world.admin(card["id"])
    assert codes(detail) == ["extraction_failed"]
    attempts = world.stand.application.settings.screenshot_control.extraction_max_attempts
    assert world.vlm.calls == ["frame"] * attempts
    assert world.clock.now - started >= timedelta(seconds=15 + 60 + 300)  # паузы 15 с, 60 с, 5 мин
    assert detail["extraction"]["status"] == "failed" and "500" in detail["extraction"]["error"]
    assert (await world.summary())["pending_review"] == 1


async def test_a_screen_without_outcome_waits_then_is_decided_or_expires(world: World) -> None:
    answer = desktop(CHAT, REVIEW, yesterday(6, 39))
    image = world.vlm.register(screen(10), answer, {"url": chat_url(CHAT), "chat_id": CHAT})
    waiting = await world.web("manager", image, outcome=None)
    assert (waiting["status"], waiting["fields"]["chat_tail"]) == ("awaiting_outcome", CHAT[-6:])
    assert (await world.summary())["awaiting_outcome"] == 1
    headers = await world.stand.sign_in("admin")
    pipeline = (await world.stand.client.get(f"{ADMIN}/pipeline", headers=headers)).json()
    assert pipeline["stages"] == {"processing": 0, "extracted": 0}, "ожидание исхода — не очередь конвейера"

    manager = await world.stand.sign_in("manager")
    chosen = await world.stand.client.patch(
        f"{MANAGER}/submissions/{waiting['id']}/outcome", json={"outcome": "amended"}, headers=manager
    )
    assert chosen.json()["status"] == "processing"
    await world.pump()
    decided = await world.card("manager", waiting["id"])
    assert (decided["status"], decided["outcome"]) == ("accepted", "amended")
    assert (await world.summary())["counts"]["amended"] == 1

    other = world.vlm.register(
        screen(11),
        desktop(OTHER_CHAT, OTHER_REVIEW, yesterday(9, 0)),
        {"url": chat_url(OTHER_CHAT), "chat_id": OTHER_CHAT},
    )
    forgotten = await world.web("manager", other, outcome=None)
    assert forgotten["status"] == "awaiting_outcome"
    config = world.stand.application.settings.screenshot_control
    later = datetime.now(UTC) + timedelta(hours=config.outcome_ttl_hours, minutes=1)
    assert await expire_outcomes(world.stand.database, config, later) == 1
    expired = await world.card("manager", forgotten["id"])
    assert (expired["status"], expired["reason_code"], expired["reason_text"]) == (
        "rejected",
        "no_outcome",
        "Не выбран исход",
    )
    locked = await world.stand.client.patch(
        f"{MANAGER}/submissions/{forgotten['id']}/outcome", json={"outcome": "positive"}, headers=manager
    )
    assert locked.status_code == 409


async def test_the_frame_and_the_address_bar_disagreeing_go_to_review(world: World) -> None:
    image = world.vlm.register(
        screen(12), desktop(CHAT, REVIEW, yesterday(6, 39)), {"url": chat_url(THIRD_CHAT), "chat_id": THIRD_CHAT}
    )

    card = await world.web("manager", image)

    assert card["status"] == "needs_review"
    detail = await world.admin(card["id"])
    assert codes(detail) == ["extraction_disagreement_chat_id"]
    assert detail["fields"]["chat_id"] == THIRD_CHAT, "при расхождении верим вырезке"
    assert detail["agreement"]["chat_id"] < 0.8


async def test_admin_decisions_change_the_manager_summary(world: World) -> None:
    one = world.vlm.register(
        screen(13), desktop(CHAT, REVIEW, yesterday(6, 39)), {"url": chat_url(THIRD_CHAT), "chat_id": THIRD_CHAT}
    )
    two = world.vlm.register(
        screen(14), desktop(OTHER_CHAT, OTHER_REVIEW, yesterday(8, 0)), {"url": chat_url(CHAT), "chat_id": CHAT}
    )
    first = await world.web("manager", one)
    second = await world.web("manager", two, outcome="deleted")
    assert first["status"] == second["status"] == "needs_review"
    before = await world.summary()
    assert (before["pending_review"], before["counts"]["accepted_total"], before["rejected"]) == (2, 0, 0)

    admin = await world.stand.sign_in("admin")
    approved = await world.stand.client.post(
        f"{ADMIN}/submissions/{first['id']}/resolve", json={"decision": "approve"}, headers=admin
    )
    assert approved.status_code == 200, approved.text
    declined = await world.stand.client.post(
        f"{ADMIN}/submissions/{second['id']}/resolve", json={"decision": "decline", "note": "чат не тот"}, headers=admin
    )
    assert declined.status_code == 200, declined.text

    after = await world.summary()
    assert (after["pending_review"], after["counts"]["accepted_total"], after["counts"]["positive"]) == (0, 1, 1)
    assert after["rejected"] == 1
    assert (await world.card("manager", first["id"]))["status"] == "accepted"
    assert await world.queue("review") == []
    events = (await world.admin(second["id"]))["events"]
    assert [event["to_verdict"] for event in events][-1] == "rejected"


@pytest.mark.parametrize(("mode", "preliminary"), [("shadow", True), ("enforce", False)])
async def test_shadow_mode_marks_the_summary_as_preliminary(tmp_path: Path, mode: str, preliminary: bool) -> None:
    async for world in _world(tmp_path, mode=mode):
        image = world.vlm.register(
            screen(15), desktop(CHAT, REVIEW, yesterday(6, 39)), {"url": chat_url(CHAT), "chat_id": CHAT}
        )
        assert (await world.web("manager", image))["status"] == "accepted", "вердикты считаются в обоих режимах"
        summary = await world.summary()
        assert (summary["mode"], summary["preliminary"], summary["counts"]["accepted_total"]) == (mode, preliminary, 1)
