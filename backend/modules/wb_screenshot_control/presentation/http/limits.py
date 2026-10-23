"""Лимиты на сотрудника поверх лимита на IP: загрузки, опрос статуса, коды сопряжения.

IP-рубеж (повышенный бюджет для офиса за одним адресом) — в `RateLimitMiddleware`; здесь второй,
по пользователю, общий для веба и расширения. Недоступный Redis — 503, как у общего лимитера.
"""

import uuid
from typing import Literal

from fastapi import Request
from redis.exceptions import RedisError

from backend.app.http.client_ip import resolve_client_ip
from backend.modules.wb_screenshot_control.presentation.http.errors import api_error
from backend.shared.settings import Settings
from backend.storage.redis import SlidingWindowRateLimiter

Budget = Literal["upload", "poll", "pairing_code"]
KEY_PREFIX = "sc:user"
PAIR_KEY_PREFIX = "rate_limit:sc-pair"


def _windows(settings: Settings, budget: Budget) -> list[tuple[str, int, int]]:
    screens = settings.screenshot_control
    if budget == "upload":
        return [("upload-minute", screens.uploads_per_minute, 60), ("upload-hour", screens.uploads_per_hour, 3600)]
    if budget == "poll":
        return [("poll", screens.status_polls_per_minute, 60)]
    return [("pairing-code", screens.pair_attempts_per_window, screens.pair_window_seconds)]


def _limiter(request: Request, prefix: str, limit: int, window: int) -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(request.app.state.redis, limit=limit, window_seconds=window, key_prefix=prefix)


async def _check(request: Request, prefix: str, limit: int, window: int, identity: str, message: str) -> None:
    try:
        decision = await _limiter(request, prefix, limit, window).check(identity)
    except (RedisError, RuntimeError) as error:
        raise api_error(503, "limiter_unavailable", "Сервис временно недоступен") from error
    if not decision.allowed:
        raise api_error(
            429, "rate_limited", message, headers={"Retry-After": str(max(1, decision.retry_after_seconds))}
        )


async def limit_user(request: Request, settings: Settings, user_id: uuid.UUID, budget: Budget) -> None:
    message = "Слишком много скриншотов подряд, подождите немного" if budget == "upload" else "Слишком часто, подождите"
    for name, limit, window in _windows(settings, budget):
        await _check(request, f"{KEY_PREFIX}:{name}", limit, window, str(user_id), message)


async def limit_pairing(request: Request) -> None:
    """Код сопряжения — единственный вход без токена, поэтому жёсткий лимит с одного IP.

    Недоступный Redis — 503: открыть перебор кодов хуже, чем отказать.
    """
    settings: Settings = await request.state.dishka_container.get(Settings)
    screens = settings.screenshot_control
    identity = resolve_client_ip(request, behind_trusted_proxy=settings.app.trust_proxy_headers)
    await _check(
        request,
        PAIR_KEY_PREFIX,
        screens.pair_attempts_per_window,
        screens.pair_window_seconds,
        identity,
        "Слишком много попыток ввести код, попробуйте позже",
    )
