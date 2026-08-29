"""Заглушка шлюза wb-egress для тестов.

Клиенты больше не ходят в `*.wildberries.ru`: каждый вызов — это POST
`/api/v1/wb/request` на шлюз с конвертом `{seller_id, api, method, path, ...}`.
Заглушка разворачивает конверт и отвечает так, как ответил бы шлюз, донося
статус WB внутри тела: `{"status": ..., "ok": ..., "body": ...}`.
"""

import json
from collections.abc import Callable
from typing import Any

import httpx
import respx

from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.shared.settings import EgressConfig

EGRESS_URL = "https://egress.test:8443"
REQUEST_URL = f"{EGRESS_URL}/api/v1/wb/request"


def egress_config(**overrides: Any) -> EgressConfig:
    values: dict[str, Any] = {
        "base_url": EGRESS_URL,
        "jwt_secret": "test-egress-secret",
        "verify": "true",
        "request_timeout_seconds": 5,
    }
    values.update(overrides)
    return EgressConfig(**values)


def make_gateway(**overrides: Any) -> EgressGateway:
    return EgressGateway(egress_config(**overrides))


class EgressStub:
    """Правила «(method, path) -> ответ WB», как их отдал бы шлюз."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.calls: list[dict[str, Any]] = []
        self._rules: list[tuple[str, str, Any]] = []
        router.post(REQUEST_URL).mock(side_effect=self._handle)

    def on(
        self,
        method: str,
        path: str,
        *,
        status: int = 200,
        body: Any = None,
        side_effect: list[tuple[int, Any]] | None = None,
        reply: Callable[[dict[str, Any]], tuple[int, Any]] | None = None,
    ) -> None:
        """Ответ на один эндпоинт WB: фиксированный, очередь ответов или функция от конверта."""
        if reply is not None:
            resolver: Any = reply
        elif side_effect is not None:
            queue = list(side_effect)
            resolver = lambda payload: queue.pop(0)  # noqa: E731
        else:
            resolver = lambda payload: (status, body)  # noqa: E731
        self._rules.append((method.upper(), path, resolver))

    def requests_to(self, path: str) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["path"] == path]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        self.calls.append(payload)
        for method, path, resolver in self._rules:
            if payload["method"] == method and payload["path"] == path:
                wb_status, wb_body = resolver(payload)
                return httpx.Response(200, json={"status": wb_status, "ok": wb_status < 400, "body": wb_body})
        return httpx.Response(200, json={"status": 404, "ok": False, "body": None})
