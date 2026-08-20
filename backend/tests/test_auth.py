import uuid
from datetime import UTC, datetime
from typing import cast

import pytest

from backend.modules.platform.application import (
    AuthenticationError,
    AuthService,
    LoginAttemptLimiter,
    LoginRateLimitError,
    PasswordService,
    TokenService,
)
from backend.modules.platform.domain import User
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.shared.settings import load_settings
from backend.storage.redis import RedisClient


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def getdel(self, key: str) -> str | None:
        self.ttls.pop(key, None)
        return self.values.pop(key, None)

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        self.values[key] = value
        if ttl_seconds is not None:
            self.ttls[key] = ttl_seconds

    async def delete(self, *keys: str) -> int:
        deleted = sum(self.values.pop(key, None) is not None for key in keys)
        for key in keys:
            self.ttls.pop(key, None)
        return deleted

    def register_script(self, source: str):
        async def script(keys: list[str], args: list) -> int:
            count = int(self.values.get(keys[0], "0")) + 1
            self.values[keys[0]] = str(count)
            if count == 1:
                self.ttls[keys[0]] = int(args[0])
            return count

        return script


class BrokenRedis(FakeRedis):
    async def get(self, key: str) -> str | None:
        raise RuntimeError("Redis is not connected")


class FakeUsers:
    def __init__(self, user: User) -> None:
        self.user = user

    async def get_by_username(self, username: str) -> User | None:
        return self.user if username == self.user.username else None

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return self.user if user_id == self.user.id else None

    async def mark_login(self, user_id: uuid.UUID) -> None:
        pass


def token_service(redis: FakeRedis) -> TokenService:
    settings = load_settings("backend/shared/settings/config.test.yaml")
    return TokenService(settings, cast(RedisClient, redis))


def make_user(passwords: PasswordService, password: str = "correct horse battery staple") -> User:
    return User(
        id=uuid.uuid4(),
        username="admin",
        password_hash=passwords.hash(password),
        is_active=True,
        created_at=datetime.now(UTC),
        last_login_at=None,
    )


def test_passwords_are_hashed_with_argon2() -> None:
    passwords = PasswordService()
    password_hash = passwords.hash("correct horse battery staple")

    assert password_hash.startswith("$argon2")
    assert passwords.verify("correct horse battery staple", password_hash)
    assert not passwords.verify("wrong password", password_hash)


async def test_access_tokens_are_validated_and_refresh_tokens_are_single_use() -> None:
    redis = FakeRedis()
    tokens = token_service(redis)
    user_id = uuid.uuid4()

    access_token = tokens.issue_access(user_id)
    refresh_token = await tokens.issue_refresh(user_id)

    assert tokens.decode_access(access_token) == user_id
    assert await tokens.rotate_refresh(refresh_token) == user_id
    assert redis.values == {}
    with pytest.raises(AuthenticationError):
        await tokens.rotate_refresh(refresh_token)

    revoked = await tokens.issue_refresh(user_id)
    await tokens.revoke_refresh(revoked)
    with pytest.raises(AuthenticationError):
        await tokens.rotate_refresh(revoked)


async def test_refresh_rotates_the_refresh_token_and_rejects_the_old_one() -> None:
    redis = FakeRedis()
    tokens = token_service(redis)
    passwords = PasswordService()
    user = make_user(passwords)
    auth = AuthService(cast(UserRepository, FakeUsers(user)), passwords, tokens)
    old_token = await tokens.issue_refresh(user.id)

    session = await auth.refresh(old_token)

    assert session.refresh_token != old_token
    assert await tokens.rotate_refresh(session.refresh_token) == user.id
    with pytest.raises(AuthenticationError):
        await auth.refresh(old_token)


async def test_logout_denylists_the_access_token_for_the_rest_of_its_life() -> None:
    redis = FakeRedis()
    tokens = token_service(redis)
    passwords = PasswordService()
    user = make_user(passwords)
    auth = AuthService(cast(UserRepository, FakeUsers(user)), passwords, tokens)
    access_token = tokens.issue_access(user.id)
    claims = tokens.decode_access_claims(access_token)

    assert not await tokens.is_access_revoked(claims.jti)
    await auth.logout(None, access_token)

    assert await tokens.is_access_revoked(claims.jti)
    settings = load_settings("backend/shared/settings/config.test.yaml")
    assert 0 < redis.ttls[f"auth:denylist:{claims.jti}"] <= settings.auth.access_token_ttl_seconds


async def test_denylist_fails_open_when_redis_is_unavailable() -> None:
    tokens = token_service(BrokenRedis())

    assert not await tokens.is_access_revoked("some-jti")


async def test_login_failures_lock_the_login_and_success_resets_the_counter() -> None:
    redis = FakeRedis()
    limiter = LoginAttemptLimiter(cast(RedisClient, redis), max_failures=3, window_seconds=60)

    await limiter.ensure_allowed("Admin")
    for _ in range(3):
        await limiter.record_failure("Admin")

    with pytest.raises(LoginRateLimitError):
        await limiter.ensure_allowed(" admin ")

    await limiter.reset("admin")
    await limiter.ensure_allowed("admin")


async def test_login_limiter_fails_open_when_redis_is_unavailable() -> None:
    limiter = LoginAttemptLimiter(cast(RedisClient, BrokenRedis()), max_failures=1, window_seconds=60)

    await limiter.record_failure("admin")
    await limiter.ensure_allowed("admin")
