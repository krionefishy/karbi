import uuid
from dataclasses import dataclass
from pathlib import Path

from backend.storage.redis.client import RedisClient, RedisScript

_SCRIPT_PATH = Path(__file__).with_name("scripts") / "sliding_window.lua"


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0
    remaining: int = 0


class SlidingWindowRateLimiter:
    def __init__(
        self,
        redis_client: RedisClient,
        limit: int,
        window_seconds: int,
        key_prefix: str = "rate_limit:ip",
    ) -> None:
        if limit < 1 or window_seconds < 1:
            raise ValueError("Rate limit and window must be positive")
        self._redis = redis_client
        self._limit = limit
        self._window_ms = window_seconds * 1000
        self._key_prefix = key_prefix
        self._script: RedisScript | None = None

    async def check(self, identity: str) -> RateLimitDecision:
        if self._script is None:
            source = _SCRIPT_PATH.read_text(encoding="utf-8")
            self._script = self._redis.register_script(source)
        result = await self._script(
            keys=[f"{self._key_prefix}:{identity}"],
            args=[self._window_ms, self._limit, uuid.uuid4().hex],
        )
        return RateLimitDecision(
            allowed=bool(result[0]),
            retry_after_seconds=int(result[1]),
            remaining=int(result[2]),
        )
