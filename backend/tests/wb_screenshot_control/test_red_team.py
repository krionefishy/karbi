"""«Красная команда»: намеренные подделки и обходы против настоящего `decide` и настоящих нормализаторов.

Каждая попытка проходит тот же путь, что в воркере, кроме сети и базы: ответ модели (как его вернул бы
Cloud.ru) → `Extractor` с нормализацией chatId, времени и текста → `decide` против уже принятых скринов.
Модель здесь — фейк, отдающий заранее заготовленный JSON: проверяется не зрение, а то, что делают правила
с тем, что зрение прочитало. dHash считается по настоящим синтетическим картинкам (Pillow).

Что НЕ ловится (закреплено тестами `test_hole_*` с `xfail(strict=True)`: отчёт pytest показывает их как
XFAIL, а если дыру закроют — тест упадёт и напомнит обновить этот список):

- правка DOM нового кейса: менеджер правит текст/время чужого или выдуманного отзыва в кабинете и
  присылает как свой — это новый кейс, сравнивать не с чем (сверки с WB нет по решению заказчика);
- чужой кейс первым: менеджер Б присылает кейс менеджера А раньше самого А — засчитан Б, а настоящий
  автор получает дубль (`cross_user_duplicate` подсвечивает пару админу, но вердикт у Б остаётся accepted);
- подпись не того исхода: «удалён» вместо «дополнен» — по содержимому подпись не проверяется;
- переписанный целиком текст в том же чате с другим временем: для правил это другой отзыв того же
  покупателя (коллизия 1 из 7.6), а не подделка;
- часы расширения, отстающие меньше чем на 72 ч: неотличимы от выгрузки из оффлайн-очереди.
"""

import hashlib
import io
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from PIL import Image, ImageFilter

from backend.modules.wb_screenshot_control.application.extractor import Extractor
from backend.modules.wb_screenshot_control.application.pipeline import queued_at
from backend.modules.wb_screenshot_control.application.thumbnails import render
from backend.modules.wb_screenshot_control.domain.decide import decide
from backend.modules.wb_screenshot_control.domain.pipeline_types import (
    Candidate,
    ClientHints,
    Decision,
    DecisionInput,
)
from backend.modules.wb_screenshot_control.domain.prompt import ADDRESS_BAR_PROMPT_V1
from backend.shared.llm import ImagePart, LLMResult
from backend.shared.settings.config import ScreenshotControlConfig
from backend.tests.wb_screenshot_control.fakes import encode, screen
from backend.tests.wb_screenshot_control.test_end_to_end import (
    CHAT,
    OTHER_CHAT,
    OTHER_REVIEW,
    REVIEW,
    THIRD_CHAT,
    chat_url,
    desktop,
    mobile,
)

CFG = ScreenshotControlConfig(audit_percent=0.0)
MSK = ZoneInfo("Europe/Moscow")
NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)  # 12:00 МСК
MANAGER_A = uuid.UUID(int=0xA)
MANAGER_B = uuid.UUID(int=0xB)
MOBILE_OPENING = [
    ("client", "Здравствуйте, пришла куртка с порванным швом на рукаве", "21:05"),
    ("seller", "Добрый вечер! Пришлите, пожалуйста, фото", "21:10"),
    ("client", "Фото приложила, шов разошёлся сантиметров на пять", "21:15"),
]


def at(hour: int, minute: int, days_ago: int = 1) -> datetime:
    moment = NOW.astimezone(MSK) - timedelta(days=days_ago)
    return moment.replace(hour=hour, minute=minute, second=0, microsecond=0)


def address(chat_id: str) -> dict[str, Any]:
    return {"url": chat_url(chat_id), "chat_id": chat_id}


class FakeVision:
    """Отдаёт заготовленный JSON вместо Cloud.ru: кадр целиком или вырезка адресной строки."""

    model = "fake-vlm"

    def __init__(self, frame: dict[str, Any], crop: dict[str, Any] | None) -> None:
        self.frame = frame
        self.crop = crop

    async def complete_vision(self, system: str, user_text: str, images: Sequence[ImagePart], **_: Any) -> LLMResult:
        answer = self.crop if system == ADDRESS_BAR_PROMPT_V1 else self.frame
        return LLMResult(json.dumps(answer or {"url": None, "chat_id": None}, ensure_ascii=False), self.model, 1, 1, 1)


@dataclass
class Shot:
    """Одна загрузка: что прочитала бы модель и что пришло от клиента."""

    answer: dict[str, Any]
    crop: dict[str, Any] | None = None
    channel: str = "web"
    url: str | None = None
    user: uuid.UUID = MANAGER_A
    outcome: str | None = "positive"
    image: bytes | None = None
    received_at: datetime = NOW
    captured_at: datetime | None = None
    uploads_last_hour: int = 1


@dataclass
class Ledger:
    """Уже решённые скрины — кандидаты для следующих, как их вернул бы репозиторий."""

    pool: list[Candidate] = field(default_factory=list)
    counter: int = 0

    async def submit(self, shot: Shot) -> Decision:
        self.counter += 1
        image = shot.image or screen(1000 + self.counter)
        received = shot.received_at + timedelta(seconds=self.counter)  # порядок поступления
        extractor = Extractor(FakeVision(shot.answer, shot.crop), timezone="Europe/Moscow", hmac_key="red-team-key")
        hints = ClientHints(url=shot.url, captured_at=shot.captured_at)
        extraction = await extractor.extract(image, channel=shot.channel, hints=hints, received_at=received)
        sha = hashlib.sha256(image).hexdigest()
        dhash = render(image).dhash
        submission_id = uuid.UUID(int=self.counter)
        inp = DecisionInput(
            submission_id=submission_id,
            user_id=shot.user,
            channel=shot.channel,  # type: ignore[arg-type]
            received_at=received,
            outcome=shot.outcome,
            file_sha256=sha,
            dhash=dhash,
            extraction=extraction,
            hints=hints,
            uploads_last_hour=shot.uploads_last_hour,
            queued_at=queued_at(shot.captured_at, received),
        )
        decision = decide(inp, self.pool, CFG)
        if decision.verdict != "rejected":
            self.pool.append(
                Candidate(
                    submission_id=submission_id,
                    user_id=shot.user,
                    received_at=received,
                    verdict=decision.verdict,
                    outcome=shot.outcome,
                    file_sha256=sha,
                    dhash=dhash,
                    fields=extraction.fields,
                )
            )
        return decision


def codes(decision: Decision) -> list[str]:
    return [reason.code for reason in decision.reasons]


ORIGINAL_IMAGE = screen(1)


def original() -> Shot:
    """Честный скрин окна: чат CHAT, отзыв REVIEW вчера в 06:39, менеджер А, «полож»."""
    return Shot(desktop(CHAT, REVIEW, at(6, 39)), address(CHAT), image=ORIGINAL_IMAGE)


async def after_original(attempt: Shot, *, first: Shot | None = None) -> Decision:
    ledger = Ledger()
    assert (await ledger.submit(first or original())).verdict == "accepted"
    return await ledger.submit(attempt)


def recoded(image: bytes) -> bytes:
    with Image.open(io.BytesIO(image)) as source:
        return encode(source.convert("RGB"), "JPEG", 75)


def cropped(image: bytes) -> bytes:
    with Image.open(io.BytesIO(image)) as source:
        width, height = source.size
        return encode(source.crop((40, 30, width - 60, height - 20)).convert("RGB"))


def photographed(image: bytes) -> bytes:
    """«Фото экрана»: поворот, размытие и пересжатие."""
    with Image.open(io.BytesIO(image)) as source:
        tilted = source.convert("RGB").rotate(3, expand=True, fillcolor=(30, 30, 30))
        return encode(tilted.filter(ImageFilter.GaussianBlur(2)), "JPEG", 60)


def flags(answer: dict[str, Any], **quality: bool) -> dict[str, Any]:
    return {**answer, "quality": {**answer["quality"], **quality}}


# --- попытка → ожидаемый вердикт и ключевая причина -------------------------------------------------------

Case = tuple[str, Shot, str, str | None]


def cases() -> list[Case]:
    text_moment = at(6, 39)
    return [
        # Повторы и пересылка одного кейса
        (
            "тот же файл тем же менеджером",
            Shot(desktop(CHAT, REVIEW, text_moment), address(CHAT), image=ORIGINAL_IMAGE),
            "duplicate",
            "exact_file",
        ),
        (
            "тот же файл другим менеджером",
            Shot(desktop(CHAT, REVIEW, text_moment), address(CHAT), image=ORIGINAL_IMAGE, user=MANAGER_B),
            "duplicate",
            "cross_user_duplicate",
        ),
        (
            "пересжатый JPEG того же кадра",
            Shot(desktop(CHAT, REVIEW, text_moment), address(CHAT), image=recoded(ORIGINAL_IMAGE)),
            "duplicate",
            "case_key",
        ),
        (
            "обрезанный кадр того же кейса",
            Shot(desktop(CHAT, REVIEW, text_moment), address(CHAT), image=cropped(ORIGINAL_IMAGE)),
            "duplicate",
            "case_key",
        ),
        (
            "повтор через час",
            Shot(desktop(CHAT, REVIEW, text_moment), address(CHAT), received_at=NOW + timedelta(hours=1)),
            "duplicate",
            "case_key",
        ),
        (
            "повтор через 5 дней",
            Shot(desktop(CHAT, REVIEW, text_moment), address(CHAT), received_at=NOW + timedelta(days=5)),
            "duplicate",
            "case_key",
        ),
        (
            "перенос кейса другому менеджеру",
            Shot(desktop(CHAT, REVIEW, text_moment), address(CHAT), user=MANAGER_B),
            "duplicate",
            "cross_user_duplicate",
        ),
        (
            "тот же кейс через расширение",
            Shot(desktop(None, REVIEW, text_moment), channel="extension", url=chat_url(CHAT), captured_at=NOW),
            "duplicate",
            "case_key",
        ),
        # Подмена chatId
        (
            "chatId другого чата в адресе расширения",
            Shot(desktop(None, REVIEW, text_moment), channel="extension", url=chat_url(OTHER_CHAT), captured_at=NOW),
            "needs_review",
            "same_text_time_other_chat",
        ),
        (
            "chatId другого чата на картинке",
            Shot(desktop(OTHER_CHAT, REVIEW, text_moment), address(OTHER_CHAT)),
            "needs_review",
            "same_text_time_other_chat",
        ),
        (
            "chatId с одной путаной буквой (O вместо 0)",
            Shot(desktop(CHAT.replace("0", "O", 1), REVIEW, text_moment), address(CHAT.replace("0", "O", 1))),
            "duplicate",
            "case_key",
        ),
        (
            "разные chatId на кадре и в адресной строке",
            Shot(desktop(OTHER_CHAT, OTHER_REVIEW, at(9, 15)), address(THIRD_CHAT)),
            "needs_review",
            "extraction_disagreement_chat_id",
        ),
        (
            "адрес расширения — не чат WB",
            Shot(
                desktop(None, OTHER_REVIEW, at(9, 15)),
                channel="extension",
                url="https://seller.wildberries.ru/feedbacks",
                captured_at=NOW,
            ),
            "needs_review",
            "url_not_chat",
        ),
        (
            "адресная строка спрятана (нет chatId)",
            Shot(desktop(None, OTHER_REVIEW, at(9, 15))),
            "needs_review",
            "chat_id_unreadable",
        ),
        # Правка времени
        (
            "время сдвинуто на 3 часа",
            Shot(desktop(CHAT, REVIEW, at(9, 39)), address(CHAT)),
            "needs_review",
            "same_chat_same_text_other_time",
        ),
        (
            "время сдвинуто на минуту",
            Shot(desktop(CHAT, REVIEW, at(6, 38)), address(CHAT)),
            "needs_review",
            "same_chat_same_text_other_time",
        ),
        # 6 и 8 путает чтение мелкого шрифта: такая правка неотличима от ошибки модели и считается тем же временем
        (
            "час 06 → 08 (цифры путаницы)",
            Shot(desktop(CHAT, REVIEW, at(8, 39)), address(CHAT)),
            "duplicate",
            "case_key",
        ),
        (
            "дата сдвинута на день",
            Shot(desktop(CHAT, REVIEW, at(6, 39, days_ago=2)), address(CHAT)),
            "needs_review",
            "same_chat_same_text_other_time",
        ),
        (
            "дата отзыва в будущем",
            Shot(desktop(THIRD_CHAT, OTHER_REVIEW, at(18, 0, days_ago=-1)), address(THIRD_CHAT)),
            "needs_review",
            "review_date_implausible",
        ),
        (
            "отзыв старше 120 дней",
            Shot(desktop(THIRD_CHAT, OTHER_REVIEW, at(18, 0, days_ago=150)), address(THIRD_CHAT)),
            "needs_review",
            "review_date_implausible",
        ),
        # Правка текста
        (
            "замена одного слова",
            Shot(desktop(CHAT, REVIEW.replace("кривые", "неровные"), text_moment), address(CHAT)),
            "duplicate",
            "case_key",
        ),
        (
            "замена пары слов",
            Shot(
                desktop(CHAT, REVIEW.replace("кривые", "неровные").replace("тонкая", "плотная"), text_moment),
                address(CHAT),
            ),
            "duplicate",
            "case_key",
        ),
        (
            "замена трёх слов",
            Shot(
                desktop(
                    CHAT,
                    REVIEW.replace("кривые", "неровные").replace("тонкая", "плотная").replace("просвечивает", "мнётся"),
                    text_moment,
                ),
                address(CHAT),
            ),
            "needs_review",
            "possible_duplicate",
        ),
        (
            "замена слова и другое время",
            Shot(desktop(CHAT, REVIEW.replace("кривые", "неровные"), at(11, 20)), address(CHAT)),
            "needs_review",
            "same_chat_same_text_other_time",
        ),
        ("регистр букв", Shot(desktop(CHAT, REVIEW.upper(), text_moment), address(CHAT)), "duplicate", "case_key"),
        (
            "пунктуация и эмодзи",
            Shot(desktop(CHAT, REVIEW.replace(",", "!!!") + " 😡👎", text_moment), address(CHAT)),
            "duplicate",
            "case_key",
        ),
        (
            "лишние пробелы и переносы",
            Shot(desktop(CHAT, REVIEW.replace(" ", "   \n "), text_moment), address(CHAT)),
            "duplicate",
            "case_key",
        ),
        (
            "невидимые символы внутри слов",
            Shot(desktop(CHAT, REVIEW.replace("а", "а​").replace("о", "­о"), text_moment), address(CHAT)),
            "duplicate",
            "case_key",
        ),
        (
            "ё вместо е и наоборот",
            Shot(desktop(CHAT, REVIEW.replace("е", "ё"), text_moment), address(CHAT)),
            "duplicate",
            "case_key",
        ),
        (
            "латинские двойники букв",
            Shot(desktop(CHAT, REVIEW.replace("о", "o").replace("а", "a"), text_moment), address(CHAT)),
            "needs_review",
            "possible_duplicate",
        ),
        (
            "усечённый текст «… Показать полностью»",
            Shot(desktop(CHAT, REVIEW[:60] + "… Показать полностью", text_moment), address(CHAT)),
            "duplicate",
            "case_key",
        ),
        # Подпись
        (
            "тот же кейс с другой подписью, тот же менеджер",
            Shot(desktop(CHAT, REVIEW, text_moment), address(CHAT), image=recoded(ORIGINAL_IMAGE), outcome="deleted"),
            "needs_review",
            "caption_conflict",
        ),
        (
            "тот же кейс с другой подписью, другой менеджер",
            Shot(desktop(CHAT, REVIEW, text_moment), address(CHAT), user=MANAGER_B, outcome="amended"),
            "duplicate",
            "caption_conflict",
        ),
        # Подлинность кадра
        (
            "фото экрана",
            Shot(
                flags(desktop(THIRD_CHAT, OTHER_REVIEW, at(9, 15)), photo_of_screen=True),
                address(THIRD_CHAT),
                image=photographed(screen(77)),
            ),
            "needs_review",
            "photo_of_screen",
        ),
        (
            "фото экрана того же кейса",
            Shot(
                flags(desktop(CHAT, REVIEW, text_moment), moire_or_glare=True),
                address(CHAT),
                image=photographed(ORIGINAL_IMAGE),
            ),
            "duplicate",
            "case_key",
        ),
        (
            "следы правки на кадре",
            Shot(flags(desktop(THIRD_CHAT, OTHER_REVIEW, at(9, 15)), looks_edited=True), address(THIRD_CHAT)),
            "needs_review",
            "possible_tampering",
        ),
        (
            "кадр без панели отзыва",
            Shot(
                flags(
                    {
                        **desktop(None, "", at(9, 15)),
                        "review": {
                            **desktop(None, "", at(9, 15))["review"],
                            "present": False,
                            "text": None,
                            "date_text": None,
                            "day": None,
                            "month": None,
                            "hour": None,
                            "minute": None,
                        },
                    },
                    cropped_missing_panel=True,
                )
            ),
            "rejected",
            "cropped_missing_panel",
        ),
        (
            "не скрин чата WB",
            Shot({**desktop(None, OTHER_REVIEW, at(9, 15)), "layout": "other", "is_wb_seller_chat": False}),
            "rejected",
            "not_wb_screenshot",
        ),
        # Поведение
        (
            "61 загрузка за час",
            Shot(desktop(THIRD_CHAT, OTHER_REVIEW, at(9, 15)), address(THIRD_CHAT), uploads_last_hour=61),
            "needs_review",
            "upload_burst",
        ),
        (
            "часы расширения спешат на 40 минут",
            Shot(
                desktop(None, OTHER_REVIEW, at(9, 15)),
                channel="extension",
                url=chat_url(THIRD_CHAT),
                captured_at=NOW + timedelta(minutes=40),
            ),
            "needs_review",
            "clock_skew",
        ),
        (
            "выгрузка из очереди через 4 дня",
            Shot(
                desktop(None, OTHER_REVIEW, at(9, 15)),
                channel="extension",
                url=chat_url(THIRD_CHAT),
                captured_at=NOW - timedelta(days=4),
            ),
            "needs_review",
            "late_upload",
        ),
        # Телефон
        (
            "телефон: продолжение того же диалога",
            Shot(
                mobile(
                    [
                        *MOBILE_OPENING,
                        ("seller", "Оформите возврат", "21:30"),
                        ("client", "Спасибо, оформила возврат", "21:40"),
                    ]
                )
            ),
            "duplicate",
            "messages_overlap",
        ),
        (
            "телефон: короткая реплика без истории",
            Shot(mobile([("client", "Ужасно", "22:10")], buyer="Ольга Смирнова")),
            "needs_review",
            "weak_key",
        ),
    ]


@pytest.mark.parametrize(("title", "shot", "verdict", "code"), cases(), ids=[case[0] for case in cases()])
async def test_attempt(title: str, shot: Shot, verdict: str, code: str | None) -> None:
    first = Shot(mobile(MOBILE_OPENING)) if shot.answer["layout"] == "mobile_app" else None
    decision = await after_original(shot, first=first)
    assert (decision.verdict, code in codes(decision)) == (verdict, True), (title, decision.verdict, codes(decision))


def test_the_list_is_long_enough() -> None:
    assert len(cases()) + len(HOLES) >= 30


async def test_a_phone_screen_of_another_buyer_with_the_same_words_is_a_new_case() -> None:
    """Контроль ложного срабатывания: другой покупатель, тот же шаблонный текст — не дубль."""
    other = Shot(mobile(MOBILE_OPENING, buyer="Ольга Смирнова"), user=MANAGER_B)
    decision = await after_original(other, first=Shot(mobile(MOBILE_OPENING)))
    assert decision.verdict != "duplicate", codes(decision)


async def test_an_honest_second_review_in_the_same_chat_is_accepted() -> None:
    """Контроль: тот же покупатель (чат), другой отзыв в другое время — честный новый кейс."""
    decision = await after_original(Shot(desktop(CHAT, OTHER_REVIEW, at(15, 2)), address(CHAT)))
    assert decision.verdict == "accepted", codes(decision)


# --- дыры: то, что правила не ловят ------------------------------------------------------------------------

HOLES = (
    "правка DOM нового кейса",
    "чужой кейс первым",
    "подпись не того исхода",
    "переписанный текст в том же чате",
    "отстающие часы расширения",
)


@pytest.mark.xfail(strict=True, reason="ДЫРА: правка DOM нового кейса — сравнивать не с чем, сверки с WB нет")
async def test_hole_dom_edit_of_a_new_case() -> None:
    ledger = Ledger()
    forged = Shot(
        desktop(THIRD_CHAT, "Выдуманный отзыв о товаре, которого покупатель никогда не писал", at(10, 0)),
        address(THIRD_CHAT),
    )
    decision = await ledger.submit(forged)
    assert decision.verdict != "accepted"


@pytest.mark.xfail(strict=True, reason="ДЫРА: чужой кейс первым — засчитан тому, кто прислал раньше")
async def test_hole_someone_elses_case_first() -> None:
    ledger = Ledger()
    stolen = await ledger.submit(replace(original(), user=MANAGER_B))
    author = await ledger.submit(replace(original(), image=recoded(ORIGINAL_IMAGE)))
    assert "cross_user_duplicate" in codes(author)  # пару админ увидит в очереди дублей
    assert stolen.verdict != "accepted"


@pytest.mark.xfail(strict=True, reason="ДЫРА: подпись по содержимому не проверяется (решение заказчика)")
async def test_hole_wrong_caption() -> None:
    decision = await Ledger().submit(replace(original(), outcome="deleted"))  # отзыв на экране не удалён
    assert decision.verdict != "accepted"


@pytest.mark.xfail(strict=True, reason="ДЫРА: текст переписан целиком — для правил это другой отзыв того же покупателя")
async def test_hole_rewritten_text_in_the_same_chat() -> None:
    rewritten = "Ткань оказалась очень тонкой, по размеру не подошло, отправила назад продавцу через пункт"
    decision = await after_original(Shot(desktop(CHAT, rewritten, at(13, 5)), address(CHAT)))
    assert decision.verdict != "accepted"


@pytest.mark.xfail(strict=True, reason="ДЫРА: отставание часов меньше 72 ч неотличимо от оффлайн-очереди")
async def test_hole_lagging_extension_clock() -> None:
    lagging = Shot(
        desktop(None, OTHER_REVIEW, at(9, 15)),
        channel="extension",
        url=chat_url(THIRD_CHAT),
        captured_at=NOW - timedelta(hours=5),
    )
    decision = await Ledger().submit(lagging)
    assert decision.verdict != "accepted"


def test_holes_are_documented() -> None:
    assert __doc__ is not None
    for hole in ("правка DOM нового кейса", "чужой кейс первым", "подпись не того исхода"):
        assert hole in __doc__
