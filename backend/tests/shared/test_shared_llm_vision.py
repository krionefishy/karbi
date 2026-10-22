import base64
import json

import httpx
import pytest
import respx

from backend.shared.llm import (
    ImagePart,
    LLMAnswerError,
    LLMClient,
    LLMError,
    LLMRequestError,
    LLMTimeoutError,
    LLMTransientError,
    extract_json_object,
)
from backend.shared.settings.config import VisionConfig

URL = "https://vlm.test/v1/chat/completions"
SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
PNG = ImagePart(b"\x89PNG\r\n\x1a\nfake", "image/png")


def config(**overrides: object) -> VisionConfig:
    values: dict[str, object] = {"base_url": "https://vlm.test/v1", "api_key": "secret", "max_tokens": 300}
    values.update(overrides)
    return VisionConfig(**values)  # type: ignore[arg-type]


def answer(content: str | None, *, tokens: tuple[int, int] = (1000, 40)) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "Qwen/Qwen3.6-35B-A3B",
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]},
        },
    )


@respx.mock
async def test_the_vision_request_matches_what_stage_zero_proved_to_work() -> None:
    route = respx.post(URL).mock(return_value=answer('{"ok": true}'))

    result = await LLMClient(config()).complete_vision(
        "система", "Извлеки поля.", [PNG], response_schema=SCHEMA, schema_name="screenshot"
    )

    sent = json.loads(route.calls.last.request.read())
    assert route.calls.last.request.headers["Authorization"] == "Bearer secret"
    assert sent["model"] == "Qwen/Qwen3.6-35B-A3B" and sent["temperature"] == 0 and sent["max_tokens"] == 300
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}
    assert sent["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "screenshot", "strict": True, "schema": SCHEMA},
    }
    system, user = sent["messages"]
    assert system == {"role": "system", "content": "система"}
    assert user["role"] == "user" and user["content"][0] == {"type": "text", "text": "Извлеки поля."}
    url = user["content"][1]["image_url"]["url"]
    assert user["content"][1]["type"] == "image_url" and url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == PNG.data
    assert result.text == '{"ok": true}' and (result.tokens_in, result.tokens_out) == (1000, 40)
    assert result.model == "Qwen/Qwen3.6-35B-A3B"


@respx.mock
async def test_the_model_can_be_overridden_per_call_for_the_repair_request() -> None:
    route = respx.post(URL).mock(return_value=answer('{"ok": true}'))

    await LLMClient(config()).complete_vision("s", "u", [PNG], model="Qwen/Qwen3.5-397B-A17B")

    sent = json.loads(route.calls.last.request.read())
    assert sent["model"] == "Qwen/Qwen3.5-397B-A17B" and "response_format" not in sent


@respx.mock
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('<think>смотрю на картинку</think>\n{"ok": true}', '{"ok": true}'),
        ('```json\n{"ok": true}\n```', '{"ok": true}'),
        (
            'Вот ответ: {"ok": true, "note": "скобка } в строке"} Надеюсь, помог.',
            '{"ok": true, "note": "скобка } в строке"}',
        ),
        ('<think>долго… {не json}</think>{"ok": false}', '{"ok": false}'),
    ],
)
async def test_wrapping_around_the_json_is_cut_off(content: str, expected: str) -> None:
    respx.post(URL).mock(return_value=answer(content))

    result = await LLMClient(config()).complete_vision("s", "u", [PNG], response_schema=SCHEMA)

    assert result.text == expected


def test_an_unclosed_object_is_returned_for_the_caller_to_reject() -> None:
    assert extract_json_object('ответ {"ok": tr') == '{"ok": tr'
    assert extract_json_object("нет объекта") == "нет объекта"


@respx.mock
async def test_an_empty_answer_is_retried_once_with_a_doubled_token_limit() -> None:
    route = respx.post(URL).mock(
        side_effect=[answer("<think>рассуждения до упора", tokens=(1000, 300)), answer('{"ok": true}')]
    )

    result = await LLMClient(config()).complete_vision("s", "u", [PNG], response_schema=SCHEMA)

    limits = [json.loads(call.request.read())["max_tokens"] for call in route.calls]
    assert limits == [300, 600]
    assert result.text == '{"ok": true}' and (result.tokens_in, result.tokens_out) == (2000, 340)


@respx.mock
async def test_two_empty_answers_are_an_answer_error_not_a_service_failure() -> None:
    route = respx.post(URL).mock(return_value=answer(None))

    with pytest.raises(LLMAnswerError):
        await LLMClient(config()).complete_vision("s", "u", [PNG])
    assert route.call_count == 2


@respx.mock
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(429), LLMTransientError),
        (httpx.Response(500), LLMTransientError),
        (httpx.Response(503), LLMTransientError),
        (httpx.Response(400), LLMRequestError),
        (httpx.Response(413), LLMRequestError),
        (httpx.Response(401), LLMRequestError),
        (httpx.Response(200, text="not json"), LLMAnswerError),
        (httpx.Response(200, json={"choices": []}), LLMAnswerError),
    ],
)
async def test_failures_are_typed_for_the_retry_policy(response: httpx.Response, error: type[LLMError]) -> None:
    route = respx.post(URL).mock(return_value=response)

    with pytest.raises(error) as caught:
        await LLMClient(config()).complete_vision("s", "u", [PNG])
    assert route.call_count == 1
    if response.status_code != 200:
        assert caught.value.status_code == response.status_code


@respx.mock
async def test_a_timeout_and_a_network_failure_are_transient() -> None:
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(LLMTimeoutError):
        await LLMClient(config()).complete_vision("s", "u", [PNG])

    respx.post(URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(LLMTransientError):
        await LLMClient(config()).complete_vision("s", "u", [PNG])


async def test_without_a_key_nothing_is_sent_and_retrying_is_pointless() -> None:
    with pytest.raises(LLMRequestError, match="ключ"):
        await LLMClient(config(api_key="")).complete_vision("s", "u", [PNG])


@respx.mock
async def test_the_per_call_timeout_reaches_the_transport() -> None:
    route = respx.post(URL).mock(return_value=answer('{"ok": true}'))

    async with httpx.AsyncClient() as http:
        await LLMClient(config(), client=http).complete_vision("s", "u", [PNG], timeout_seconds=7)

    assert route.calls.last.request.extensions["timeout"]["read"] == 7
