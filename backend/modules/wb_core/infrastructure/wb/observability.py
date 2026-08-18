import logging
from dataclasses import dataclass

import httpx


@dataclass(frozen=True, slots=True)
class RateLimitSnapshot:
    """What WB told us about our token bucket on one response.

    `limit` is the burst capacity, not a quota: WB refills the bucket to
    `limit` over `reset` seconds, so one token comes back every
    `reset / limit` seconds.
    """

    limit: int | None = None
    remaining: int | None = None
    reset: float | None = None
    retry: float | None = None

    @property
    def interval_seconds(self) -> float | None:
        if self.reset is None or not self.limit:
            return None
        return self.reset / self.limit

    @property
    def known(self) -> bool:
        return any(value is not None for value in (self.limit, self.remaining, self.reset, self.retry))


def _number(response: httpx.Response, header: str) -> float | None:
    raw = response.headers.get(header)
    if raw is None:
        return None
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return None


def read_rate_limit(logger: logging.Logger, response: httpx.Response, *, path: str) -> RateLimitSnapshot:
    """Parse and log the X-Ratelimit-* headers.

    A 4XX costs ten times a normal request against the bucket, so a 429 is
    logged loudly: it is never just a retry, it digs the hole deeper.
    """
    limit = _number(response, "X-Ratelimit-Limit")
    remaining = _number(response, "X-Ratelimit-Remaining")
    reset = _number(response, "X-Ratelimit-Reset")
    retry = _number(response, "X-Ratelimit-Retry")
    if retry is None:
        retry = _number(response, "Retry-After")
    snapshot = RateLimitSnapshot(
        limit=int(limit) if limit is not None else None,
        remaining=int(remaining) if remaining is not None else None,
        reset=reset,
        retry=retry,
    )
    if snapshot.known or response.status_code == 429:
        logger.log(
            logging.WARNING if response.status_code == 429 else logging.DEBUG,
            "wb_rate_limit",
            extra={
                "path": path,
                "status": response.status_code,
                "limit": snapshot.limit,
                "remaining": snapshot.remaining,
                "reset": snapshot.reset,
                "retry": snapshot.retry,
            },
        )
    return snapshot
