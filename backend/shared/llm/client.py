"""Клиент языковой модели по OpenAI-совместимому API.

Не знает ни про бота поддержки, ни про его промпты: получает системную
инструкцию и текст, возвращает ответ с учётом расхода. Ошибки приводит к одному
типу, чтобы вызывающий код отличал «модель не ответила» от своих багов.
"""

import re
import time
from dataclasses import dataclass

import httpx

from backend.shared.settings.config import LLMConfig

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMError(Exception):
    """Модель не дала пригодного ответа: сеть, статус, пустой или неразборчивый ответ."""


@dataclass(frozen=True, slots=True)
class LLMResult:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int


class LLMClient:
    def __init__(self, config: LLMConfig, *, client: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._client = client

    @property
    def model(self) -> str:
        return self.config.model

    async def complete(self, system: str, user: str) -> LLMResult:
        """Один запрос без повторов: повтор решает вызывающий код, чтобы не множить расход."""
        if not self.config.api_key:
            raise LLMError("ключ модели не задан")
        body = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        started = time.monotonic()
        try:
            if self._client is not None:
                response = await self._client.post(self._url(), json=body, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                    response = await client.post(self._url(), json=body, headers=headers)
        except httpx.HTTPError as error:
            raise LLMError(f"запрос к модели не прошёл: {type(error).__name__}") from error
        if response.status_code != 200:
            raise LLMError(f"модель ответила статусом {response.status_code}")
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise LLMError("ответ модели не разобран") from error
        text = _THINK.sub("", content or "").strip()
        if not text:
            raise LLMError("модель вернула пустой ответ")
        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            model=str(data.get("model") or self.config.model),
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    def _url(self) -> str:
        return self.config.base_url.rstrip("/") + "/chat/completions"
