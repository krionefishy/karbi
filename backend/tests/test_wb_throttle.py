import asyncio

import pytest

from backend.modules.wb_core.infrastructure.wb import (
    WBBudget,
    WBThrottle,
    WBThrottleTimeout,
    budgets_for,
    scope_for_key,
)
from backend.modules.wb_core.infrastructure.wb.observability import RateLimitSnapshot


def test_scope_never_leaks_the_api_key() -> None:
    scope = scope_for_key("very-secret-token")
    assert "very-secret-token" not in scope
    assert scope == scope_for_key("very-secret-token")
    assert scope != scope_for_key("another-token")


def test_budgets_are_split_between_key_and_host() -> None:
    budgets = budgets_for("feedbacks", per_key=10, per_host=4, window_seconds=60)
    assert budgets["feedbacks:key"] == WBBudget(10, 60)
    assert budgets["feedbacks:host"] == WBBudget(4, 60)


async def test_throttle_lets_the_budget_through_without_waiting() -> None:
    throttle = WBThrottle(budgets={"feedbacks:key": WBBudget(3, 60)})

    await asyncio.wait_for(
        asyncio.gather(*(throttle.acquire(("feedbacks:key", "seller")) for _ in range(3))),
        timeout=1,
    )


async def test_throttle_gives_up_when_the_wait_is_unreasonable(monkeypatch) -> None:
    slept: list[float] = []

    async def record(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr("backend.modules.wb_core.infrastructure.wb.throttle.asyncio.sleep", record)
    throttle = WBThrottle(budgets={"feedbacks:host": WBBudget(1, 60)}, max_wait_seconds=5)

    await throttle.acquire(("feedbacks:host", "all"))
    with pytest.raises(WBThrottleTimeout):
        await throttle.acquire(("feedbacks:host", "all"))

    # Better to hand the job to the retry queue than to park a worker for a
    # minute on a bucket that will not refill in time.
    assert not slept


async def test_throttle_paces_from_the_headers_wb_returned(monkeypatch) -> None:
    slept: list[float] = []

    async def record(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr("backend.modules.wb_core.infrastructure.wb.throttle.asyncio.sleep", record)
    throttle = WBThrottle(budgets={"feedbacks:key": WBBudget(1000, 60)})

    # Burst 6 refilled over 2 s means one token every 0.33 s. With tokens to
    # spare we keep bursting; on the last one we wait out the interval.
    throttle.observe("feedbacks:key", "seller", RateLimitSnapshot(limit=6, remaining=4, reset=2.0))
    await throttle.acquire(("feedbacks:key", "seller"))
    assert not slept

    throttle.observe("feedbacks:key", "seller", RateLimitSnapshot(limit=6, remaining=0, reset=2.0))
    await throttle.acquire(("feedbacks:key", "seller"))
    assert slept == [pytest.approx(2 / 6, abs=0.05)]


async def test_throttle_waits_exactly_as_long_as_a_429_asked(monkeypatch) -> None:
    slept: list[float] = []

    async def record(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr("backend.modules.wb_core.infrastructure.wb.throttle.asyncio.sleep", record)
    throttle = WBThrottle(budgets={"feedbacks:key": WBBudget(1000, 60)}, max_wait_seconds=60)

    throttle.observe("feedbacks:key", "seller", RateLimitSnapshot(limit=6, remaining=0, reset=29.0, retry=2.0))
    await throttle.acquire(("feedbacks:key", "seller"))

    assert slept == [pytest.approx(2, abs=0.05)]


async def test_throttle_refuses_a_bucket_that_refills_too_slowly() -> None:
    throttle = WBThrottle(budgets={"feedbacks:key": WBBudget(1000, 60)}, max_wait_seconds=120)

    # A basic token: burst 1 restored over 402 s. No pacing makes a full scan
    # possible, so the client is told to stop rather than block for minutes.
    throttle.observe("feedbacks:key", "seller", RateLimitSnapshot(limit=1, remaining=0, reset=402.0))
    with pytest.raises(WBThrottleTimeout):
        await throttle.acquire(("feedbacks:key", "seller"))


async def test_throttle_keeps_separate_budgets_per_scope() -> None:
    throttle = WBThrottle(budgets={"feedbacks:key": WBBudget(1, 60)})

    await asyncio.wait_for(
        asyncio.gather(
            throttle.acquire(("feedbacks:key", "seller-a")),
            throttle.acquire(("feedbacks:key", "seller-b")),
        ),
        timeout=1,
    )


async def test_throttle_ignores_buckets_it_was_not_given() -> None:
    throttle = WBThrottle(budgets={})
    await asyncio.wait_for(throttle.acquire(("content:key", "seller")), timeout=1)
