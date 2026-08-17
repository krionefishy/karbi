import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from backend.modules.platform.domain import User
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.shared.settings import Settings
from backend.storage.redis import RedisClient


class AuthenticationError(Exception):
    pass


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


class TokenService:
    _refresh_prefix = "auth:refresh:"

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
            return uuid.UUID(payload["sub"])
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as error:
            raise AuthenticationError from error

    async def issue_refresh(self, user_id: uuid.UUID) -> str:
        token = secrets.token_urlsafe(48)
        await self._redis.set(
            self._refresh_key(token),
            str(user_id),
            ttl_seconds=self._config.refresh_token_ttl_seconds,
        )
        return token

    async def rotate_refresh(self, token: str) -> tuple[uuid.UUID, str]:
        user_id = await self._redis.getdel(self._refresh_key(token))
        if user_id is None:
            raise AuthenticationError
        try:
            parsed_user_id = uuid.UUID(user_id)
        except ValueError as error:
            raise AuthenticationError from error
        return parsed_user_id, await self.issue_refresh(parsed_user_id)

    async def revoke_refresh(self, token: str) -> None:
        await self._redis.delete(self._refresh_key(token))

    def _refresh_key(self, token: str) -> str:
        digest = hashlib.sha256(token.encode()).hexdigest()
        return f"{self._refresh_prefix}{digest}"


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
        user_id, new_refresh_token = await self._tokens.rotate_refresh(refresh_token)
        user = await self._users.get_by_id(user_id)
        if user is None or not user.is_active:
            await self._tokens.revoke_refresh(new_refresh_token)
            raise AuthenticationError
        return AuthSession(user, self._tokens.issue_access(user.id), new_refresh_token)

    async def current_user(self, access_token: str) -> User:
        user = await self._users.get_by_id(self._tokens.decode_access(access_token))
        if user is None or not user.is_active:
            raise AuthenticationError
        return user

    async def logout(self, refresh_token: str | None) -> None:
        if refresh_token:
            await self._tokens.revoke_refresh(refresh_token)

    async def _create_session(self, user: User) -> AuthSession:
        return AuthSession(
            user=user,
            access_token=self._tokens.issue_access(user.id),
            refresh_token=await self._tokens.issue_refresh(user.id),
        )
