import json

import httpx
import pytest
import respx

from backend.shared.llm import LLMClient, LLMError, mask_pii
from backend.shared.settings.config import LLMConfig

URL = "https://llm.test/v1/chat/completions"


def config(**overrides: object) -> LLMConfig:
    values: dict[str, object] = {"base_url": "https://llm.test/v1/", "model": "model-a", "api_key": "secret"}
    values.update(overrides)
    return LLMConfig(**values)  # type: ignore[arg-type]


def answer(content: str, *, model: str = "model-a") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 5},
        },
    )


@respx.mock
async def test_the_request_carries_the_model_key_and_both_messages() -> None:
    route = respx.post(URL).mock(return_value=answer('{"ok": true}'))

    result = await LLMClient(config()).complete("инструкция", "текст")

    request = route.calls.last.request
    sent = json.loads(request.read())
    assert request.headers["Authorization"] == "Bearer secret"
    assert sent["model"] == "model-a" and sent["temperature"] == 0 and sent["max_tokens"] == 150
    assert [(m["role"], m["content"]) for m in sent["messages"]] == [("system", "инструкция"), ("user", "текст")]
    assert result.text == '{"ok": true}' and (result.tokens_in, result.tokens_out) == (12, 5)
    assert result.model == "model-a" and result.latency_ms >= 0


@respx.mock
async def test_the_model_is_taken_from_the_config_not_from_the_code() -> None:
    route = respx.post(URL).mock(return_value=answer("да", model="model-b"))

    result = await LLMClient(config(model="model-b")).complete("s", "u")

    assert json.loads(route.calls.last.request.read())["model"] == "model-b"
    assert result.model == "model-b"


@respx.mock
async def test_reasoning_blocks_are_dropped_from_the_answer() -> None:
    respx.post(URL).mock(return_value=answer('<think>долго думаю</think>\n{"verdict": "open"}'))

    assert (await LLMClient(config()).complete("s", "u")).text == '{"verdict": "open"}'


@respx.mock
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(429),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": "  "}}]}),
    ],
)
async def test_every_bad_answer_is_one_error_type(response: httpx.Response) -> None:
    respx.post(URL).mock(return_value=response)

    with pytest.raises(LLMError):
        await LLMClient(config()).complete("s", "u")


@respx.mock
async def test_a_network_failure_is_an_llm_error_and_is_not_retried() -> None:
    route = respx.post(URL).mock(side_effect=httpx.ConnectTimeout("slow"))

    with pytest.raises(LLMError):
        await LLMClient(config()).complete("s", "u")
    assert route.call_count == 1


async def test_without_a_key_nothing_is_sent() -> None:
    with pytest.raises(LLMError, match="ключ"):
        await LLMClient(config(api_key="")).complete("s", "u")


def test_masking_hides_contacts_ids_and_long_numbers_but_keeps_the_sense() -> None:
    text = (
        "Позвоните +7 912 345-67-89 или напишите ivan.petrov@mail.ru, номер заявки "
        "ee002091-7908-4db0-8840-7fe8e8156ab4, карта 4276 1234 5678 9012, "
        "детали https://example.com/order/1 . Заявка одобрена, сумма 500 рублей."
    )

    masked = mask_pii(text)

    assert "912" not in masked and "mail.ru" not in masked and "4db0" not in masked and "4276" not in masked
    assert "example.com" not in masked
    assert "<EMAIL>" in masked and "<ID>" in masked and "<URL>" in masked
    assert masked.count("<NUM>") == 2
    assert "Заявка одобрена, сумма 500 рублей." in masked


def test_masking_leaves_ordinary_text_alone() -> None:
    text = "Здравствуйте! Мы готовы вернуть 1000 рублей в течение 3 дней."
    assert mask_pii(text) == text
