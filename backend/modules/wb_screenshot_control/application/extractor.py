"""Распознавание скриншота: картинка → `ExtractionResult` с нормализованными полями (архитектура, 6).

Один вызов модели по кадру целиком; для окна браузера — второй по вырезке адресной строки, он же второе
мнение о chatId (своего OCR нет: решение после этапа 0, шов `OcrEngine` оставлен). Для расширения адрес
страницы приходит от клиента и chatId берётся из него, с картинки — только отзыв, время и текст.

Сбои сервиса модели (`LLMTransientError`: сеть, таймаут, 429, 5xx; `LLMRequestError`: прочие 4xx, нет ключа)
пробрасываются как есть — повторы с паузой решает конвейер. Ответ, который не разобрать и после одного
ремонтного запроса, или пустой ответ — `status='failed'`, это не сбой инфраструктуры.
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol, TypeVar
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from backend.modules.wb_screenshot_control.domain.normalize import (
    ScreenDateTime,
    buyer_hmac,
    chat_id_agreement,
    chat_id_from_image,
    chat_id_from_url,
    datetime_agreement,
    has_truncation_tail,
    normalize_article,
    normalize_text,
    parse_screen_datetime,
    resolve_screen_datetime,
    screen_datetime_from_numbers,
    text_agreement,
)
from backend.modules.wb_screenshot_control.domain.pipeline_types import (
    ChatMessage,
    ClientHints,
    ExtractedFields,
    ExtractionResult,
    QualityFlags,
)
from backend.modules.wb_screenshot_control.domain.prompt import (
    ADDRESS_BAR_PROMPT_V1,
    ADDRESS_BAR_SCHEMA_V1,
    PROMPT_V1,
    PROMPT_VERSION,
    SCREENSHOT_SCHEMA_V1,
    AddressBarAnswerV1,
    ClientMessage,
    LayoutHint,
    ScreenshotAnswerV1,
    VisibleMessage,
    repair_text,
    user_text_v1,
)
from backend.modules.wb_screenshot_control.infrastructure.vision import (
    Frame,
    ImageUnreadable,
    address_bar_part,
    full_frame_part,
    open_frame,
)
from backend.shared.llm import ImagePart, LLMAnswerError, LLMClient, LLMResult
from backend.shared.settings.config import Settings

Answer = TypeVar("Answer", bound=BaseModel)
Field = Literal["chat_id", "time", "text"]


class VisionModel(Protocol):
    """То, что нужно от клиента модели; `LLMClient` подходит как есть."""

    @property
    def model(self) -> str: ...

    async def complete_vision(
        self,
        system: str,
        user_text: str,
        images: Sequence[ImagePart],
        *,
        response_schema: dict[str, Any] | None = None,
        schema_name: str = "answer",
        model: str | None = None,
        max_tokens: int | None = None,
        timeout_seconds: int | None = None,
    ) -> LLMResult: ...


class OcrEngine(Protocol):
    """Шов под локальный OCR как третье мнение (tesseract и т. п.). Не реализован: вторым мнением служит
    чтение вырезки адресной строки. Результат положили бы в `ExtractionResult.ocr_json`."""

    async def read(self, image: bytes) -> dict[str, Any]: ...


@dataclass(slots=True)
class _Usage:
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0

    def add(self, result: LLMResult) -> None:
        self.model = self.model or result.model
        self.tokens_in += result.tokens_in
        self.tokens_out += result.tokens_out
        self.latency_ms += result.latency_ms


def layout_hint(width: int, height: int) -> LayoutHint:
    """Подсказка модели по пропорциям: окно 16:9 / 16:10 — компьютер, узкий портрет — телефон."""
    ratio = width / height if height else 0
    if ratio >= 1.2:
        return "desktop"
    if 0 < ratio <= 0.75:
        return "mobile"
    return "unknown"


class Extractor:
    def __init__(
        self,
        vision: VisionModel,
        *,
        timezone: str,
        hmac_key: str,
        text_threshold: float = 0.8,
        max_image_side: int = 2000,
        repair_model: str | None = None,
    ) -> None:
        self.vision = vision
        self.timezone = timezone
        self.hmac_key = hmac_key
        self.text_threshold = text_threshold
        self.max_image_side = max_image_side
        self.repair_model = repair_model

    async def extract(
        self, image: bytes, *, channel: str, hints: ClientHints, received_at: datetime
    ) -> ExtractionResult:
        try:
            frame = await asyncio.to_thread(open_frame, image)
        except ImageUnreadable:
            return ExtractionResult(status="failed", vlm_prompt=PROMPT_VERSION, error="image_unreadable")
        usage = _Usage()
        url_chat_id = chat_id_from_url(hints.url) if channel == "extension" else None
        part = await asyncio.to_thread(full_frame_part, frame, self.max_image_side)
        user_text = user_text_v1(layout_hint=layout_hint(*frame.size), url_known=url_chat_id is not None)
        try:
            answer = await self._ask(ScreenshotAnswerV1, SCREENSHOT_SCHEMA_V1, PROMPT_V1, user_text, part, usage)
        except LLMAnswerError:
            return self._failed(usage, "empty_answer")
        if answer is None:
            return self._failed(usage, "invalid_json")
        address = None
        if url_chat_id is None and answer.layout == "desktop_window":
            address = await self._read_address_bar(frame, usage)
        fields = self._fields(answer, address, url_chat_id, received_at)
        return ExtractionResult(
            status="ok",
            fields=fields,
            vlm_model=usage.model,
            vlm_prompt=PROMPT_VERSION,
            # Имя покупателя в открытом виде не хранится нигде: только HMAC в полях.
            vlm_json=answer.model_dump(mode="json", exclude={"buyer_name"}),
            ocr_json={"source": "vlm_address_bar", **address.model_dump(mode="json")} if address else None,
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            latency_ms=usage.latency_ms,
        )

    async def _ask(
        self,
        schema_model: type[Answer],
        schema: dict[str, Any],
        system: str,
        user_text: str,
        part: ImagePart,
        usage: _Usage,
    ) -> Answer | None:
        """Запрос и строгий разбор; не разобралось — один ремонтный запрос (запасной моделью, если задана)."""
        result = await self.vision.complete_vision(
            system, user_text, [part], response_schema=schema, schema_name=schema_model.__name__
        )
        usage.add(result)
        try:
            return schema_model.model_validate_json(result.text)
        except ValidationError as error:
            problem = "; ".join(f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in error.errors())
        repaired = await self.vision.complete_vision(
            system,
            f"{user_text}\n\n{repair_text(problem)}",
            [part],
            response_schema=schema,
            schema_name=schema_model.__name__,
            model=self.repair_model,
        )
        usage.add(repaired)
        try:
            return schema_model.model_validate_json(repaired.text)
        except ValidationError:
            return None

    async def _read_address_bar(self, frame: Frame, usage: _Usage) -> AddressBarAnswerV1 | None:
        part = await asyncio.to_thread(address_bar_part, frame)
        try:
            return await self._ask(
                AddressBarAnswerV1, ADDRESS_BAR_SCHEMA_V1, ADDRESS_BAR_PROMPT_V1, "Прочитай адрес.", part, usage
            )
        except LLMAnswerError:
            return None

    def _fields(
        self,
        answer: ScreenshotAnswerV1,
        address: AddressBarAnswerV1 | None,
        url_chat_id: str | None,
        received_at: datetime,
    ) -> ExtractedFields:
        agreement: dict[str, float] = {}
        chat_id, chat_source = self._chat_id(answer, address, url_chat_id, agreement)
        # Телефон: ключ кейса — последнее сообщение покупателя (7.5), второе мнение — оно же в списке переписки.
        # Десктоп: блок отзыва (7.4), время сверяется между текстом «как на экране» и числами.
        mobile = answer.layout == "mobile_app"
        client = answer.last_client_message if mobile else None
        twin = _last_visible_client(answer.messages) if mobile else None
        if client is not None and not _readable(client):
            client = None
        if client is not None or twin is not None:
            if client is None:
                assert twin is not None
                client = ClientMessage(present=True, text=twin.text, time_text=twin.time_text, date_text=twin.date_text)
                twin = None
            text = client.text
            moment = _pick(_message_datetime(client.date_text, client.time_text), _numbers(client))
            if twin is not None:
                _put(agreement, "time", datetime_agreement(moment, _message_datetime(twin.date_text, twin.time_text)))
                _put(agreement, "text", text_agreement(client.text, twin.text))
            truncated = has_truncation_tail(text)
        else:
            review = answer.review
            text = review.text
            from_text = parse_screen_datetime(review.date_text)
            from_numbers = _numbers(review)
            _put(agreement, "time", datetime_agreement(from_text, from_numbers))
            moment = _pick(from_text, from_numbers)
            truncated = review.text_truncated or has_truncation_tail(text)
        review_at, precision = resolve_screen_datetime(moment, received_at=received_at, timezone=self.timezone)
        raw_text = text.strip() if text and text.strip() else None
        names: tuple[Field, ...] = ("chat_id", "time", "text")
        disagreements = tuple(name for name in names if name in agreement and agreement[name] < self.text_threshold)
        return ExtractedFields(
            layout=answer.layout,
            is_wb_seller_chat=answer.is_wb_seller_chat,
            chat_id=chat_id,
            chat_id_source=chat_source,
            review_at=review_at,
            review_time_precision=precision,
            stars=answer.review.stars,
            review_text=raw_text,
            text_norm=normalize_text(raw_text) or None,
            text_truncated=truncated,
            article_norm=normalize_article(answer.article.value) or normalize_article(answer.review.article),
            buyer_hmac=buyer_hmac(answer.buyer_name.value, self.hmac_key),
            messages=tuple(
                message for item in answer.messages if (message := self._message(item, received_at)) is not None
            ),
            quality=QualityFlags(**answer.quality.model_dump()),
            agreement=agreement,
            disagreements=disagreements,
        )

    def _chat_id(
        self,
        answer: ScreenshotAnswerV1,
        address: AddressBarAnswerV1 | None,
        url_chat_id: str | None,
        agreement: dict[str, float],
    ) -> tuple[str | None, Literal["url", "image"] | None]:
        if url_chat_id is not None:
            return url_chat_id, "url"
        frame_raw = answer.chat_id.value or answer.url.value
        frame_id = chat_id_from_image(answer.chat_id.value) or chat_id_from_image(answer.url.value)
        crop_raw = (address.chat_id or address.url) if address else None
        crop_id = (chat_id_from_image(address.chat_id) or chat_id_from_image(address.url)) if address else None
        _put(agreement, "chat_id", chat_id_agreement(frame_id or frame_raw, crop_id or crop_raw))
        # Оба прочитаны и прошли проверку GUID, но разные — верим вырезке: она крупнее и читалась отдельно.
        chosen = crop_id or frame_id
        return chosen, ("image" if chosen else None)

    def _message(self, item: VisibleMessage, received_at: datetime) -> ChatMessage | None:
        text = normalize_text(item.text)
        moment = _message_datetime(item.date_text, item.time_text)
        if not text and moment is None:
            return None
        day = month = None
        if moment is not None and moment.days_ago is not None:
            resolved, _ = resolve_screen_datetime(
                ScreenDateTime(days_ago=moment.days_ago), received_at=received_at, timezone=self.timezone
            )
            if resolved is not None:
                local = resolved.astimezone(ZoneInfo(self.timezone))
                day, month = local.day, local.month
        elif moment is not None:
            day, month = moment.day, moment.month
        return ChatMessage(
            text_norm=text,
            hour=moment.hour if moment else None,
            minute=moment.minute if moment else None,
            day=day,
            month=month,
            side="client" if item.side == "client" else "seller" if item.side == "seller" else None,
        )

    def _failed(self, usage: _Usage, error: str) -> ExtractionResult:
        return ExtractionResult(
            status="failed",
            vlm_model=usage.model,
            vlm_prompt=PROMPT_VERSION,
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            latency_ms=usage.latency_ms,
            error=error,
        )


def build_extractor(settings: Settings) -> Extractor:
    screens = settings.screenshot_control
    return Extractor(
        LLMClient(settings.vision),
        timezone=screens.timezone,
        hmac_key=screens.hmac_key,
        text_threshold=screens.thresholds.vlm_ocr_text,
        max_image_side=settings.vision.max_image_side,
        repair_model=settings.vision.fallback_model or None,
    )


def _put(agreement: dict[str, float], name: str, value: float | None) -> None:
    if value is not None:
        agreement[name] = value


def _numbers(source: Any) -> ScreenDateTime | None:
    return screen_datetime_from_numbers(
        day=source.day, month=source.month, year=source.year, hour=source.hour, minute=source.minute
    )


def _message_datetime(date_text: str | None, time_text: str | None) -> ScreenDateTime | None:
    joined = " ".join(part for part in (date_text, time_text) if part)
    return parse_screen_datetime(joined) if joined else None


def _pick(primary: ScreenDateTime | None, secondary: ScreenDateTime | None) -> ScreenDateTime | None:
    """Рабочее значение: прошедшее разбор и более полное; при равенстве — как на экране (текстом)."""
    if primary is None or secondary is None:
        return primary or secondary

    def completeness(value: ScreenDateTime) -> int:
        return int(value.has_date) + int(value.has_time)

    return secondary if completeness(secondary) > completeness(primary) else primary


def _readable(message: ClientMessage) -> bool:
    return message.present and bool(message.text or message.time_text or message.hour is not None)


def _last_visible_client(messages: list[VisibleMessage]) -> VisibleMessage | None:
    return next((item for item in reversed(messages) if item.side == "client"), None)
