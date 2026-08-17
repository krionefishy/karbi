from typing import Any, cast

import redis.asyncio as redis


class RedisClient:
    def __init__(self, url: str) -> None:
        self._client = redis.from_url(url, decode_responses=True)

    @property
    def raw(self) -> redis.Redis:
        return self._client

    async def connect(self) -> None:
        await self._client.ping()

    async def disconnect(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        try:
            return bool(await self._client.ping())
        except Exception:
            return False

    async def get(self, key: str) -> str | None:
        return cast(str | None, await self._client.get(key))

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        await self._client.set(key, value, ex=ttl_seconds)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def eval(self, script: str, keys: list[str], args: list[Any]) -> Any:
        return await self._client.eval(script, len(keys), *keys, *args)
