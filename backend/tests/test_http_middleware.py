import uuid
from typing import cast

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from backend.app.http.authentication import CurrentPrincipal
from backend.app.http.middleware import AccessTokenMiddleware, RateLimitMiddleware
from backend.modules.platform.application import AuthenticationError
from backend.storage.redis import RateLimitDecision, SlidingWindowRateLimiter


class FakeTokenDecoder:
    def __init__(self, user_id: uuid.UUID) -> None:
        self.user_id = user_id

    def decode_access(self, token: str) -> uuid.UUID:
        if token != "valid-token":
            raise AuthenticationError
        return self.user_id


class FakeRateLimiter:
    def __init__(self, decision: RateLimitDecision) -> None:
        self.decision = decision
        self.identities: list[str] = []

    async def check(self, identity: str) -> RateLimitDecision:
        self.identities.append(identity)
        return self.decision


async def test_access_token_middleware_is_passive_until_route_requires_user() -> None:
    user_id = uuid.uuid4()
    app = FastAPI()
    app.add_middleware(AccessTokenMiddleware, decoder=FakeTokenDecoder(user_id))

    @app.get("/public")
    async def public(request: Request) -> dict[str, bool]:
        return {"authenticated": hasattr(request.state, "principal")}

    @app.get("/private")
    async def private(principal: CurrentPrincipal) -> dict[str, str]:
        return {"user_id": str(principal.user_id)}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        public_response = await client.get("/public", headers={"Authorization": "Bearer invalid"})
        missing_response = await client.get("/private")
        invalid_response = await client.get("/private", headers={"Authorization": "Bearer invalid"})
        valid_response = await client.get("/private", headers={"Authorization": "Bearer valid-token"})

    assert public_response.json() == {"authenticated": False}
    assert missing_response.status_code == 401
    assert invalid_response.status_code == 401
    assert valid_response.json() == {"user_id": str(user_id)}


async def test_rate_limit_middleware_sets_headers_and_rejects_excess_requests() -> None:
    allowed = FakeRateLimiter(RateLimitDecision(allowed=True, remaining=29))
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        limiter=cast(SlidingWindowRateLimiter, allowed),
        enabled=True,
        limit=30,
        behind_trusted_proxy=False,
    )

    @app.get("/resource")
    async def resource() -> dict[str, str]:
        return {"status": "ok"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/resource")

    assert response.status_code == 200
    assert response.headers["X-RateLimit-Limit"] == "30"
    assert response.headers["X-RateLimit-Remaining"] == "29"
    assert allowed.identities == ["127.0.0.1"]

    rejected = FakeRateLimiter(RateLimitDecision(allowed=False, retry_after_seconds=17))
    rejected_app = FastAPI()
    rejected_app.add_middleware(
        RateLimitMiddleware,
        limiter=cast(SlidingWindowRateLimiter, rejected),
        enabled=True,
        limit=30,
        behind_trusted_proxy=False,
    )

    @rejected_app.get("/resource")
    async def rejected_resource() -> dict[str, str]:
        return {"status": "should-not-run"}

    async with AsyncClient(transport=ASGITransport(app=rejected_app), base_url="http://test") as client:
        rejected_response = await client.get("/resource")

    assert rejected_response.status_code == 429
    assert rejected_response.headers["Retry-After"] == "17"
