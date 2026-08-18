import uuid
from typing import cast

import pytest

from backend.modules.platform.application import AuthenticationError, PasswordService, TokenService
from backend.shared.settings import load_settings
from backend.storage.redis import RedisClient


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        self.values[key] = value
        if ttl_seconds is not None:
            self.ttls[key] = ttl_seconds

    async def getex(self, key: str, ttl_seconds: int) -> str | None:
        value = self.values.get(key)
        if value is not None:
            self.ttls[key] = ttl_seconds
        return value

    async def delete(self, *keys: str) -> int:
        deleted = sum(self.values.pop(key, None) is not None for key in keys)
        for key in keys:
            self.ttls.pop(key, None)
        return deleted


def test_passwords_are_hashed_with_argon2() -> None:
    passwords = PasswordService()
    password_hash = passwords.hash("correct horse battery staple")

    assert password_hash.startswith("$argon2")
    assert passwords.verify("correct horse battery staple", password_hash)
    assert not passwords.verify("wrong password", password_hash)


async def test_access_and_refresh_tokens_are_validated_and_refresh_is_idempotent() -> None:
    settings = load_settings("backend/shared/settings/config.test.yaml")
    redis = FakeRedis()
    tokens = TokenService(settings, cast(RedisClient, redis))
    user_id = uuid.uuid4()

    access_token = tokens.issue_access(user_id)
    refresh_token = await tokens.issue_refresh(user_id)

    assert tokens.decode_access(access_token) == user_id
    assert await tokens.renew_refresh(refresh_token) == user_id
    assert await tokens.renew_refresh(refresh_token) == user_id
    assert len(redis.values) == 1

    await tokens.revoke_refresh(refresh_token)
    with pytest.raises(AuthenticationError):
        await tokens.renew_refresh(refresh_token)
