import base64
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from backend.shared.settings.config import LLMConfig, VisionConfig

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
_OPEN_THINK = re.compile(r"<think>.*\Z", re.DOTALL)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class LLMError(Exception):
    """Модель не дала пригодного ответа: сеть, статус, пустой или неразборчивый ответ.

    Подклассы говорят вызывающему коду, есть ли смысл повторять: `LLMTransientError` — да, с паузой;
    `LLMRequestError` — нет, запрос или настройка неверны; `LLMAnswerError` — модель ответила, но не тем.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMTransientError(LLMError):
    """Сеть, таймаут, 429 или 5xx: сервис модели сейчас недоступен, повтор позже может пройти."""


class LLMTimeoutError(LLMTransientError):
    """Модель не уложилась в таймаут."""


class LLMRequestError(LLMError):
    """4xx (кроме 429) или не задан ключ: тот же запрос снова не пройдёт."""


class LLMAnswerError(LLMError):
    """Ответ 200, но пустой или не разбирается."""


@dataclass(frozen=True, slots=True)
class LLMResult:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


@dataclass(frozen=True, slots=True)
class ImagePart:
    """Картинка для запроса: байты и их MIME-тип, в запрос уходит как data URL."""

    data: bytes
    mime: str = "image/png"

    def data_url(self) -> str:
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode('ascii')}"


def strip_wrapping(text: str) -> str:
    """Убирает рассуждения `<think>` (в том числе незакрытые) и markdown-ограду вокруг ответа."""
    text = _THINK.sub("", text or "")
    text = _OPEN_THINK.sub("", text).strip()
    return _FENCE.sub("", text).strip()


def extract_json_object(text: str) -> str:
    """Вырезает первый сбалансированный `{…}` из ответа: часть моделей обрамляет JSON пояснениями.

    Если объекта нет, возвращает текст без обёртки как есть — разбор и ошибка остаются вызывающему.
    """
    text = strip_wrapping(text)
    start = text.find("{")
    if start < 0:
        return text
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return text[start:]


class LLMClient:
    def __init__(self, config: LLMConfig | VisionConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = client

    @property
    def model(self) -> str:
        return self.config.model

    async def complete(self, system: str, user: str) -> LLMResult:
        """Один запрос без повторов: повтор решает вызывающий код, чтобы не множить расход."""
        body = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        started = time.monotonic()
        data = await self._post(body, wait_seconds=self.config.timeout_seconds)
        content, tokens_in, tokens_out = _content(data)
        text = _THINK.sub("", content).strip()
        if not text:
            raise LLMAnswerError("модель вернула пустой ответ")
        return LLMResult(
            text=text,
            model=str(data.get("model") or self.config.model),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

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
    ) -> LLMResult:
        """Запрос с картинками. Пустой ответ (рассуждения съели `max_tokens`) повторяется один раз
        с удвоенным лимитом; остальные повторы — забота вызывающего кода по типу исключения.

        С `response_schema` ответ строгий `json_schema`, а `text` в результате — уже вырезанный `{…}`.
        """
        limit = max_tokens or self.config.max_tokens
        parts: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        parts.extend({"type": "image_url", "image_url": {"url": image.data_url()}} for image in images)
        body: dict[str, Any] = {
            "model": model or self.config.model,
            "temperature": self.config.temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": parts}],
        }
        if isinstance(self.config, VisionConfig):
            # Qwen без этого рассуждает: втрое дольше, а при малом лимите токенов ответ пустой.
            body["chat_template_kwargs"] = {"enable_thinking": self.config.enable_thinking}
        if response_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": response_schema},
            }
        started = time.monotonic()
        spent_in = spent_out = 0
        data: dict[str, Any] = {}
        text = ""
        wait = timeout_seconds or self.config.timeout_seconds
        for attempt_limit in (limit, limit * 2):
            data = await self._post({**body, "max_tokens": attempt_limit}, wait_seconds=wait)
            content, tokens_in, tokens_out = _content(data)
            text = strip_wrapping(content)
            spent_in += tokens_in
            spent_out += tokens_out
            if text:
                break
        if not text:
            raise LLMAnswerError("модель дважды вернула пустой ответ")
        return LLMResult(
            text=extract_json_object(text) if response_schema is not None else text,
            model=str(data.get("model") or body["model"]),
            tokens_in=spent_in,
            tokens_out=spent_out,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def _post(self, body: dict[str, Any], *, wait_seconds: int) -> dict[str, Any]:
        if not self.config.api_key:
            raise LLMRequestError("ключ модели не задан")
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        try:
            if self._client is not None:
                response = await self._client.post(self._url(), json=body, headers=headers, timeout=wait_seconds)
            else:
                async with httpx.AsyncClient(timeout=wait_seconds) as client:
                    response = await client.post(self._url(), json=body, headers=headers)
        except httpx.TimeoutException as error:
            raise LLMTimeoutError(f"модель не ответила за {wait_seconds} с") from error
        except httpx.HTTPError as error:
            raise LLMTransientError(f"запрос к модели не прошёл: {type(error).__name__}") from error
        status = response.status_code
        if status == 429 or status >= 500:
            raise LLMTransientError(f"модель ответила статусом {status}", status_code=status)
        if status != 200:
            raise LLMRequestError(f"модель ответила статусом {status}", status_code=status)
        try:
            data = response.json()
        except ValueError as error:
            raise LLMAnswerError("ответ модели не разобран") from error
        if not isinstance(data, dict):
            raise LLMAnswerError("ответ модели не разобран")
        return data

    def _url(self) -> str:
        return self.config.base_url.rstrip("/") + "/chat/completions"


def _content(data: dict[str, Any]) -> tuple[str, int, int]:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise LLMAnswerError("ответ модели не разобран") from error
    usage = data.get("usage") or {}
    return (
        content if isinstance(content, str) else "",
        int(usage.get("prompt_tokens") or 0),
        int(usage.get("completion_tokens") or 0),
    )
