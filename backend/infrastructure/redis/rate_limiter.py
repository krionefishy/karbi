import time
import uuid
from dataclasses import dataclass

from backend.infrastructure.redis.client import RedisClient

_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local oldest = now - window

redis.call('ZREMRANGEBYSCORE', key, '-inf', oldest)
local count = redis.call('ZCARD', key)
if count >= limit then
    local first = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after = 1
    if #first == 2 then
        retry_after = math.max(1, math.ceil((tonumber(first[2]) + window - now) / 1000))
    end
    return {0, retry_after}
end

redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window + 1000)
return {1, 0}
"""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0


class SlidingWindowRateLimiter:
    def __init__(self, redis_client: RedisClient, limit: int, window_seconds: int) -> None:
        self._redis = redis_client
        self._limit = limit
        self._window_ms = window_seconds * 1000

    async def check(self, identity: str) -> RateLimitDecision:
        now_ms = int(time.time() * 1000)
        result = await self._redis.eval(
            _SLIDING_WINDOW_SCRIPT,
            keys=[f"rate_limit:ip:{identity}"],
            args=[now_ms, self._window_ms, self._limit, uuid.uuid4().hex],
        )
        return RateLimitDecision(allowed=bool(result[0]), retry_after_seconds=int(result[1]))
