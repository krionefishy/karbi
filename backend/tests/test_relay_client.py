import json

import jwt
import pytest
import respx
from httpx import ConnectError, Response

from backend.modules.notifications.domain import (
    MessengerPermanentError,
    MessengerRateLimitError,
    MessengerTemporaryError,
)
from backend.modules.notifications.infrastructure.relay import RelayClient
from backend.shared.settings import RelayConfig

CONFIG = RelayConfig(
    base_url="https://relay.invalid",
    jwt_secret="a-relay-secret-that-is-long-enough-for-tests",
    verify="false",
)
SEND = "https://relay.invalid/api/v1/messages/send"
BOTS = "https://relay.invalid/api/v1/bots"


def client() -> RelayClient:
    return RelayClient(CONFIG)


async def send(response: Response) -> str | None:
    with respx.mock:
        respx.post(SEND).mock(return_value=response)
        return await client().send(
            bot_code="turnover-alerts", recipient="-100500", text="Остаток кончается", idempotency_key="msg-1"
        )


class TestAuthorization:
    async def test_every_call_carries_a_fresh_token_for_the_relay(self) -> None:
        with respx.mock:
            route = respx.post(SEND).mock(return_value=Response(200, json={"status": "sent", "message_ref": "1"}))
            await client().send(bot_code="b", recipient="1", text="t", idempotency_key="k")

        header = route.calls.last.request.headers["Authorization"]
        assert header.startswith("Bearer ")
        claims = jwt.decode(
            header.removeprefix("Bearer "),
            CONFIG.jwt_secret,
            algorithms=["HS256"],
            audience=CONFIG.audience,
            issuer=CONFIG.issuer,
        )
        # The audience is what keeps this token from being replayed back at us
        # through the relay's own callbacks.
        assert claims["aud"] == "relay"
        assert claims["exp"] > claims["iat"]

    async def test_a_message_carries_the_contract_and_nothing_else(self) -> None:
        with respx.mock:
            route = respx.post(SEND).mock(return_value=Response(200, json={"message_ref": "1"}))
            await client().send(bot_code="turnover-alerts", recipient="1", text="t", idempotency_key="k")

        payload = json.loads(route.calls.last.request.content)
        assert set(payload) == {"bot_code", "recipient", "text", "idempotency_key"}
        # No token, no chat object, no parse_mode: the messenger's shapes stay
        # on the other side of the wire.
        assert "token" not in route.calls.last.request.content.decode()


class TestSending:
    async def test_a_delivered_message_returns_the_reference(self) -> None:
        assert await send(Response(200, json={"status": "sent", "message_ref": "777"})) == "777"

    async def test_a_refusal_is_permanent_and_keeps_the_messenger_wording(self) -> None:
        # The dispatcher matches its dead-chat markers against this text, so the
        # relay's description has to survive the trip intact.
        with pytest.raises(MessengerPermanentError, match="bot was blocked"):
            await send(Response(422, json={"detail": "bot was blocked by the user"}))

    async def test_rate_limiting_carries_the_wait_from_the_header(self) -> None:
        with pytest.raises(MessengerRateLimitError) as error:
            await send(Response(429, json={"detail": "too many"}, headers={"Retry-After": "12"}))
        assert error.value.retry_after == 12

    @pytest.mark.parametrize(
        ("status", "detail"),
        [
            (404, "unknown bot turnover-alerts"),
            (401, "unauthorized"),
            (502, "the messenger is unreachable"),
            (500, "boom"),
        ],
    )
    async def test_everything_else_is_worth_retrying(self, status: int, detail: str) -> None:
        # A bot nobody registered on the relay yet and a skewed clock are both
        # operator problems: failing the message would throw away a notification
        # that will go through minutes later.
        with pytest.raises(MessengerTemporaryError):
            await send(Response(status, json={"detail": detail}))

    async def test_an_unreachable_relay_is_temporary(self) -> None:
        with respx.mock:
            respx.post(SEND).mock(side_effect=ConnectError("no route"))
            with pytest.raises(MessengerTemporaryError, match="relay unreachable"):
                await client().send(bot_code="b", recipient="1", text="t", idempotency_key="k")

    async def test_a_non_json_answer_does_not_crash_the_delivery_loop(self) -> None:
        with pytest.raises(MessengerTemporaryError):
            await send(Response(502, text="<html>gateway</html>"))


class TestRegistration:
    async def test_registration_returns_what_the_main_server_stores(self) -> None:
        with respx.mock:
            route = respx.post(BOTS).mock(
                return_value=Response(
                    200,
                    json={
                        "bot_code": "turnover-alerts",
                        "title": "Оборачиваемость",
                        "invite_link_template": "https://t.me/mplace_auto_bot?start={token}",
                    },
                )
            )
            bot = await client().register_bot(bot_code="turnover-alerts", token="1234:secret")

        assert bot.invite_link_template == "https://t.me/mplace_auto_bot?start={token}"
        assert bot.title == "Оборачиваемость"
        # The token goes out once, to the relay, and nowhere else.
        assert route.calls.last.request.url.path == "/api/v1/bots"

    async def test_a_token_the_relay_rejects_is_permanent(self) -> None:
        with respx.mock:
            respx.post(BOTS).mock(return_value=Response(422, json={"detail": "Unauthorized"}))
            with pytest.raises(MessengerPermanentError):
                await client().register_bot(bot_code="dead", token="1234:dead")
