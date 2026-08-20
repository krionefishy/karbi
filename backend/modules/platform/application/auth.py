import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from redis.exceptions import RedisError

from backend.modules.platform.domain import User
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.shared.settings import Settings
from backend.storage.redis import RedisClient

logger = logging.getLogger("auth")


class AuthenticationError(Exception):
    pass


class LoginRateLimitError(Exception):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(retry_after_seconds)
        self.retry_after_seconds = retry_after_seconds


class PasswordService:
    def __init__(self) -> None:
        self._password_hash = PasswordHash.recommended()
        self._dummy_hash = self._password_hash.hash(secrets.token_urlsafe(32))

    def hash(self, password: str) -> str:
        return self._password_hash.hash(password)

    def verify(self, password: str, password_hash: str | None) -> bool:
        if password_hash is None:
            self._password_hash.verify(password, self._dummy_hash)
            return False
        return self._password_hash.verify(password, password_hash)


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: uuid.UUID
    jti: str
    expires_at: datetime


class TokenService:
    _refresh_prefix = "auth:refresh:"
    _denylist_prefix = "auth:denylist:"

    def __init__(self, settings: Settings, redis: RedisClient) -> None:
        self._config = settings.auth
        self._redis = redis

    def issue_access(self, user_id: uuid.UUID) -> str:
        now = datetime.now(UTC)
        payload = {
            "sub": str(user_id),
            "jti": str(uuid.uuid4()),
            "type": "access",
            "iat": now,
            "exp": now + timedelta(seconds=self._config.access_token_ttl_seconds),
            "iss": self._config.issuer,
            "aud": self._config.audience,
        }
        return jwt.encode(payload, self._config.jwt_secret, algorithm=self._config.algorithm)

    def decode_access(self, token: str) -> uuid.UUID:
        return self.decode_access_claims(token).user_id

    def decode_access_claims(self, token: str) -> AccessClaims:
        try:
            payload = jwt.decode(
                token,
                self._config.jwt_secret,
                algorithms=[self._config.algorithm],
                issuer=self._config.issuer,
                audience=self._config.audience,
                options={"require": ["sub", "jti", "type", "iat", "exp", "iss", "aud"]},
            )
            if payload["type"] != "access":
                raise AuthenticationError
            return AccessClaims(
                user_id=uuid.UUID(payload["sub"]),
                jti=str(payload["jti"]),
                expires_at=datetime.fromtimestamp(payload["exp"], UTC),
            )
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as error:
            raise AuthenticationError from error

    async def revoke_access(self, token: str) -> None:
        """Deny the access token for the rest of its lifetime."""
        try:
            claims = self.decode_access_claims(token)
        except AuthenticationError:
            return
        ttl_seconds = int((claims.expires_at - datetime.now(UTC)).total_seconds())
        if ttl_seconds <= 0:
            return
        await self._redis.set(self._denylist_key(claims.jti), "1", ttl_seconds=ttl_seconds)

    async def is_access_revoked(self, jti: str) -> bool:
        try:
            return await self._redis.get(self._denylist_key(jti)) is not None
        except (RedisError, RuntimeError):
            logger.exception("access_denylist_unavailable")
            return False

    async def issue_refresh(self, user_id: uuid.UUID) -> str:
        token = secrets.token_urlsafe(48)
        await self._redis.set(
            self._refresh_key(token),
            str(user_id),
            ttl_seconds=self._config.refresh_token_ttl_seconds,
        )
        return token

    async def rotate_refresh(self, token: str) -> uuid.UUID:
        """Consume the refresh token: the old key is deleted atomically, a replay gets nothing."""
        user_id = await self._redis.getdel(self._refresh_key(token))
        if user_id is None:
            raise AuthenticationError
        try:
            return uuid.UUID(user_id)
        except ValueError as error:
            raise AuthenticationError from error

    async def revoke_refresh(self, token: str) -> None:
        await self._redis.delete(self._refresh_key(token))

    def _refresh_key(self, token: str) -> str:
        digest = hashlib.sha256(token.encode()).hexdigest()
        return f"{self._refresh_prefix}{digest}"

    def _denylist_key(self, jti: str) -> str:
        return f"{self._denylist_prefix}{jti}"


class LoginAttemptLimiter:
    """Failed logins per username: too many inside the window lock that login out.

    Redis being unreachable never blocks a login — the limiter fails open,
    the same way the access denylist does.
    """

    _prefix = "auth:login_failures:"
    _increment_script = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""

    def __init__(self, redis: RedisClient, max_failures: int = 10, window_seconds: int = 900) -> None:
        self._redis = redis
        self._max_failures = max_failures
        self._window_seconds = window_seconds

    async def ensure_allowed(self, username: str) -> None:
        try:
            count = await self._redis.get(self._key(username))
        except (RedisError, RuntimeError):
            logger.exception("login_limiter_unavailable")
            return
        if count is not None and count.isdigit() and int(count) >= self._max_failures:
            raise LoginRateLimitError(self._window_seconds)

    async def record_failure(self, username: str) -> None:
        try:
            script = self._redis.register_script(self._increment_script)
            await script(keys=[self._key(username)], args=[self._window_seconds])
        except (RedisError, RuntimeError):
            logger.exception("login_limiter_unavailable")

    async def reset(self, username: str) -> None:
        try:
            await self._redis.delete(self._key(username))
        except (RedisError, RuntimeError):
            logger.exception("login_limiter_unavailable")

    def _key(self, username: str) -> str:
        digest = hashlib.sha256(username.strip().lower().encode()).hexdigest()
        return f"{self._prefix}{digest}"


@dataclass(frozen=True, slots=True)
class AuthSession:
    user: User
    access_token: str
    refresh_token: str


class AuthService:
    def __init__(self, users: UserRepository, passwords: PasswordService, tokens: TokenService) -> None:
        self._users = users
        self._passwords = passwords
        self._tokens = tokens

    async def login(self, username: str, password: str) -> AuthSession:
        user = await self._users.get_by_username(username.strip())
        password_valid = self._passwords.verify(password, user.password_hash if user else None)
        if user is None or not user.is_active or not password_valid:
            raise AuthenticationError
        await self._users.mark_login(user.id)
        return await self._create_session(user)

    async def refresh(self, refresh_token: str) -> AuthSession:
        user_id = await self._tokens.rotate_refresh(refresh_token)
        user = await self._users.get_by_id(user_id)
        if user is None or not user.is_active:
            raise AuthenticationError
        return await self._create_session(user)

    async def current_user(self, user_id: uuid.UUID) -> User:
        user = await self._users.get_by_id(user_id)
        if user is None or not user.is_active:
            raise AuthenticationError
        return user

    async def logout(self, refresh_token: str | None, access_token: str | None = None) -> None:
        if refresh_token:
            await self._tokens.revoke_refresh(refresh_token)
        if access_token:
            await self._tokens.revoke_access(access_token)

    async def _create_session(self, user: User) -> AuthSession:
        return AuthSession(
            user=user,
            access_token=self._tokens.issue_access(user.id),
            refresh_token=await self._tokens.issue_refresh(user.id),
        )
