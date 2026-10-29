"""Два рубежа лимитов: повышенный бюджет на IP для приёма и расширения (кроме обмена кода) и лимит на сотрудника."""

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from redis.exceptions import RedisError

from backend.app.http.middleware import RateLimitMiddleware
from backend.app.http.setup import screenshot_uploads
from backend.storage.redis import RateLimitDecision, RedisClient, SlidingWindowRateLimiter
from backend.tests.wb_screenshot_control.fakes import BASE
from backend.tests.wb_screenshot_control.http_stand import EXTENSION, MANAGER, open_stand


@dataclass
class CountingLimiter:
    name: str
    allowed: bool = True
    seen: list[str] = field(default_factory=list)

    async def check(self, identity: str) -> RateLimitDecision:
        self.seen.append(identity)
        return RateLimitDecision(allowed=self.allowed, retry_after_seconds=7, remaining=1)


def guarded_app(general: CountingLimiter, relaxed: CountingLimiter) -> FastAPI:
    app = FastAPI()
    rule = screenshot_uploads(BASE, cast(RedisClient, None))
    app.add_middleware(
        RateLimitMiddleware,
        limiter=cast(SlidingWindowRateLimiter, general),
        enabled=True,
        limit=30,
        behind_trusted_proxy=False,
        relaxed=(replace(rule, limiter=cast(SlidingWindowRateLimiter, relaxed)),),
    )

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def anything(path: str) -> dict[str, str]:
        return {"path": path}

    return app


@pytest.mark.parametrize(
    ("method", "path", "budget"),
    [
        ("POST", f"{MANAGER}/submissions", "relaxed"),
        ("GET", f"{MANAGER}/submissions/abc", "relaxed"),
        ("POST", f"{EXTENSION}/submissions", "relaxed"),
        ("GET", f"{EXTENSION}/me", "relaxed"),
        ("POST", f"{EXTENSION}/pair", "general"),
        ("GET", f"{MANAGER}/summary", "general"),
        ("GET", "/api/v1/wb/returns/extension/heartbeat", "general"),
    ],
)
async def test_office_budget_covers_uploads_and_extension_but_not_pairing(method: str, path: str, budget: str) -> None:
    general, relaxed = CountingLimiter("general"), CountingLimiter("relaxed")
    async with AsyncClient(
        transport=ASGITransport(app=guarded_app(general, relaxed)), base_url="http://test"
    ) as client:
        response = await client.request(method, path)
    assert response.status_code == 200
    used = relaxed if budget == "relaxed" else general
    assert (len(used.seen), len(general.seen) + len(relaxed.seen)) == (1, 1)
    assert response.headers["X-RateLimit-Limit"] == ("300" if budget == "relaxed" else "30")


async def test_office_budget_still_answers_429() -> None:
    general, relaxed = CountingLimiter("general"), CountingLimiter("relaxed", allowed=False)
    async with AsyncClient(
        transport=ASGITransport(app=guarded_app(general, relaxed)), base_url="http://test"
    ) as client:
        response = await client.post(f"{MANAGER}/submissions")
    assert (response.status_code, response.headers["Retry-After"]) == (429, "7")


async def test_uploads_and_polls_are_limited_per_manager(tmp_path: Path) -> None:
    async with open_stand(
        tmp_path, uploads_per_minute=2, status_polls_per_minute=3, roles=("manager", "other")
    ) as stand:
        manager = await stand.sign_in("manager")
        other = await stand.sign_in("other")
        assert [(await stand.upload(manager, seed=seed)).status_code for seed in range(2)] == [201, 201]
        third = await stand.upload(manager, seed=3)
        assert (third.status_code, third.headers["X-Error-Code"]) == (429, "rate_limited")
        assert 0 < int(third.headers["Retry-After"]) <= 60
        assert (await stand.upload(other, seed=4)).status_code == 201

        polls = [(await stand.client.get(f"{MANAGER}/summary", headers=manager)).status_code for _ in range(4)]
        assert polls == [200, 200, 200, 429]


async def test_user_limiter_fails_closed_without_redis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async with open_stand(tmp_path, roles=("manager",)) as stand:
        manager = await stand.sign_in("manager")

        async def broken(self: SlidingWindowRateLimiter, identity: str) -> RateLimitDecision:
            raise RedisError("down")

        monkeypatch.setattr(SlidingWindowRateLimiter, "check", broken)
        response = await stand.upload(manager)
        assert (response.status_code, response.headers["X-Error-Code"]) == (503, "limiter_unavailable")
        pair = await stand.client.post(
            f"{EXTENSION}/pair", json={"code": "ABCD2345", "install_id": "chrome-install-0001", "ext_version": "0.1.0"}
        )
        assert (pair.status_code, pair.headers["X-Error-Code"]) == (503, "limiter_unavailable")
