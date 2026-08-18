from collections.abc import Awaitable, Callable, Sequence
from typing import Any, cast

import redis.asyncio as redis

RedisScript = Callable[..., Awaitable[Any]]


class RedisClient:
    def __init__(self) -> None:
        self._client: redis.Redis | None = None

    async def connect(self, url: str) -> None:
        if self._client is not None:
            return
        client = redis.from_url(url, decode_responses=True)
        await client.ping()
        self._client = client

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._client = None

    def register_script(self, source: str) -> RedisScript:
        return cast(RedisScript, self._require_client().register_script(source))

    async def ping(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    async def get(self, key: str) -> str | None:
        return cast(str | None, await self._require_client().get(key))

    async def getex(self, key: str, ttl_seconds: int) -> str | None:
        return cast(str | None, await self._require_client().getex(key, ex=ttl_seconds))

    async def getdel(self, key: str) -> str | None:
        return cast(str | None, await self._require_client().getdel(key))

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        await self._require_client().set(key, value, ex=ttl_seconds)

    async def delete(self, *keys: str) -> int:
        return int(await self._require_client().delete(*keys))

    async def scan_iter(self, match: str) -> Sequence[str]:
        return [cast(str, key) async for key in self._require_client().scan_iter(match=match)]

    def _require_client(self) -> redis.Redis:
        if self._client is None:
            raise RuntimeError("Redis is not connected")
        return self._client
