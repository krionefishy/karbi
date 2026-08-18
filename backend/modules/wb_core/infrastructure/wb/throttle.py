import asyncio
import hashlib
import logging
import random
import time
from collections import deque
from dataclasses import dataclass

from redis.exceptions import RedisError

from backend.modules.wb_core.infrastructure.wb.observability import RateLimitSnapshot
from backend.storage.redis import RedisClient, SlidingWindowRateLimiter

_KEY_PREFIX = "wb:egress"
# Below this many burst tokens we stop bursting and fall back to WB's own
# interval. A 429 costs ten requests against the bucket, so the last token is
# never worth spending.
_PACE_BELOW = 2


class WBThrottleTimeout(Exception):
    """WB will not have budget for us any time soon."""


@dataclass(frozen=True, slots=True)
class WBBudget:
    """Safety net used until WB tells us its real numbers."""

    limit: int
    window_seconds: int


def scope_for_key(api_key: str) -> str:
    """Stable per-seller bucket name that never contains the key itself."""
    return hashlib.blake2b(api_key.encode(), digest_size=8).hexdigest()


def key_bucket(api: str) -> str:
    return f"{api}:key"


def host_bucket(api: str) -> str:
    return f"{api}:host"


def budgets_for(api: str, *, per_key: int, per_host: int, window_seconds: int) -> dict[str, WBBudget]:
    return {
        key_bucket(api): WBBudget(per_key, window_seconds),
        host_bucket(api): WBBudget(per_host, window_seconds),
    }


class WBThrottle:
    """Paces outgoing Wildberries calls from WB's own accounting.

    WB runs a token bucket per API category per token: `X-Ratelimit-Limit` is
    the burst capacity and `X-Ratelimit-Reset` the time to refill it, so one
    token returns every `reset / limit` seconds. Those numbers differ sixfold
    between a personal and a basic token, which is why they are read from every
    response instead of configured. The configured budgets remain a coarse
    safety net for the very first call, and live in Redis so that several
    workers share them.
    """

    def __init__(
        self,
        *,
        budgets: dict[str, WBBudget],
        redis_client: RedisClient | None = None,
        max_wait_seconds: float = 120.0,
    ) -> None:
        self._budgets = budgets
        self._max_wait_seconds = max_wait_seconds
        self._logger = logging.getLogger("wb.throttle")
        self._local: dict[str, deque[float]] = {}
        self._next_allowed: dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._limiters: dict[str, SlidingWindowRateLimiter] = {}
        if redis_client is not None:
            self._limiters = {
                bucket: SlidingWindowRateLimiter(
                    redis_client,
                    limit=budget.limit,
                    window_seconds=budget.window_seconds,
                    key_prefix=_KEY_PREFIX,
                )
                for bucket, budget in budgets.items()
            }

    def observe(self, bucket: str, scope: str, snapshot: RateLimitSnapshot) -> None:
        """Record what WB just said and decide when the next call may go out."""
        identity = f"{bucket}:{scope}"
        delay = self._delay_from(snapshot)
        if delay is None:
            self._next_allowed.pop(identity, None)
            return
        self._next_allowed[identity] = time.monotonic() + delay

    @staticmethod
    def _delay_from(snapshot: RateLimitSnapshot) -> float | None:
        if snapshot.retry is not None:
            # Retrying earlier than asked just earns another 429.
            return snapshot.retry
        interval = snapshot.interval_seconds
        if interval is None or snapshot.remaining is None:
            return None
        if snapshot.remaining < _PACE_BELOW:
            return interval
        return None

    async def acquire(self, *buckets: tuple[str, str]) -> None:
        """Take one slot in each `(bucket, scope)` pair, waiting when the budget is spent."""
        for bucket, scope in buckets:
            await self._acquire_one(bucket, scope)

    async def _acquire_one(self, bucket: str, scope: str) -> None:
        identity = f"{bucket}:{scope}"
        waited = await self._wait_for_wb(bucket, identity)
        budget = self._budgets.get(bucket)
        if budget is None:
            return
        while True:
            delay = await self._try_acquire(bucket, identity, budget)
            if delay is None:
                return
            self._require_budget(bucket, waited + delay)
            self._logger.info("wb_throttle_wait", extra={"bucket": bucket, "delay_seconds": round(delay, 2)})
            await asyncio.sleep(delay)
            waited += delay

    async def _wait_for_wb(self, bucket: str, identity: str) -> float:
        """Hold off until the moment WB itself named in the previous response."""
        deadline = self._next_allowed.pop(identity, None)
        if deadline is None:
            return 0.0
        delay = deadline - time.monotonic()
        if delay <= 0:
            return 0.0
        self._require_budget(bucket, delay)
        self._logger.info("wb_throttle_pace", extra={"bucket": bucket, "delay_seconds": round(delay, 2)})
        await asyncio.sleep(delay)
        return delay

    def _require_budget(self, bucket: str, waited: float) -> None:
        if waited <= self._max_wait_seconds:
            return
        # A bucket refilling this slowly cannot finish a full scan. Failing now
        # hands the job to the retry queue instead of parking a worker on it
        # for the rest of the run.
        self._logger.warning(
            "wb_throttle_budget_exhausted", extra={"bucket": bucket, "waited_seconds": round(waited, 1)}
        )
        raise WBThrottleTimeout(f"WB просит ждать {waited:.0f} с — дольше допустимых {self._max_wait_seconds:.0f} с")

    async def _try_acquire(self, bucket: str, identity: str, budget: WBBudget) -> float | None:
        """Return None when a slot was taken, otherwise the seconds to wait."""
        limiter = self._limiters.get(bucket)
        if limiter is not None:
            try:
                decision = await limiter.check(identity)
            except (RedisError, RuntimeError):
                # Redis down or not connected yet — pace locally rather than
                # letting a throttling concern fail the whole sync.
                self._logger.warning("wb_throttle_redis_unavailable", extra={"bucket": bucket})
            else:
                if decision.allowed:
                    return None
                return decision.retry_after_seconds + random.uniform(0.05, 0.5)
        return await self._try_acquire_locally(identity, budget)

    async def _try_acquire_locally(self, identity: str, budget: WBBudget) -> float | None:
        async with self._lock:
            now = time.monotonic()
            window = self._local.setdefault(identity, deque())
            while window and window[0] <= now - budget.window_seconds:
                window.popleft()
            if len(window) < budget.limit:
                window.append(now)
                return None
            return max(0.05, window[0] + budget.window_seconds - now) + random.uniform(0.05, 0.5)
