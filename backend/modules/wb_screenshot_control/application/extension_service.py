"""Расширение Chrome: код сопряжения, обмен на токен `mas_…`, проверка токена и список установок.

Токен хранится только sha256. Каждый запрос расширения сверяет установку, срок простоя, версию
и активность сотрудника — блокировка сотрудника отсекает расширение сразу, а не через сутки.
"""

import hashlib
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.modules.wb_screenshot_control.application.errors import ServiceError
from backend.modules.wb_screenshot_control.domain import PAIRING_ALPHABET, TOKEN_PREFIX, version_supported
from backend.modules.wb_screenshot_control.infrastructure.postgres.models import ExtensionInstallModel
from backend.modules.wb_screenshot_control.infrastructure.postgres.queries import ExtensionStore
from backend.shared.settings.config import ScreenshotControlConfig

Clock = Callable[[], datetime]
CODE_LENGTH = 8
TOKEN_BYTES = 32
BAD_TOKEN = "Токен расширения не подходит: подключите расширение заново по коду со страницы"
CODE_INVALID = "Код не подошёл: он уже использован или устарел. Получите новый на странице"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_code() -> str:
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(CODE_LENGTH))


@dataclass(frozen=True, slots=True)
class ExtensionCaller:
    install_id: uuid.UUID
    user_id: uuid.UUID
    username: str
    ext_version: str | None


class ExtensionService:
    def __init__(self, session: AsyncSession, config: ScreenshotControlConfig, *, now: Clock = _utcnow) -> None:
        self.session = session
        self.config = config
        self.store = ExtensionStore(session)
        self.users = UserRepository(session)
        self.now = now

    async def pairing_code(self, user_id: uuid.UUID) -> dict[str, Any]:
        """Новый код гасит прежний живой код сотрудника."""
        now = self.now()
        expires_at = now + timedelta(minutes=self.config.pairing_ttl_minutes)
        await self.store.drop_codes(user_id, now)
        for _ in range(10):
            try:
                async with self.session.begin_nested():
                    pairing = await self.store.add_code(user_id, new_code(), expires_at)
            except IntegrityError:
                continue
            await self.session.commit()
            return {"code": pairing.code, "expires_at": pairing.expires_at}
        raise RuntimeError("could not allocate a pairing code")

    async def pair(self, code: str, *, install_id: str, browser: str, ext_version: str) -> dict[str, Any]:
        """Код гасится атомарно; прежняя установка того же браузера и лишние старые — отзываются."""
        minimum = self.config.extension_min_version
        if not version_supported(ext_version, minimum):
            raise ServiceError("upgrade_required", f"Обновите расширение до версии {minimum} или новее")
        now = self.now()
        pairing = await self.store.consume_code(code, now)
        if pairing is None:
            await self.session.rollback()
            raise ServiceError("code_invalid", CODE_INVALID)
        user = await self.users.get_by_id(pairing.user_id)
        if user is None or not user.is_active:
            await self.session.commit()  # код сгорает и у заблокированного
            raise ServiceError("code_invalid", CODE_INVALID)

        await self.store.revoke_same_browser(user.id, install_id, now)
        live = await self.store.live_installs(user.id)
        for stale in sorted(live, key=_last_activity)[: max(0, len(live) - self.config.max_extension_installs + 1)]:
            stale.revoked_at = now
        token = TOKEN_PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
        install = await self.store.add_install(
            user_id=user.id,
            install_id=install_id,
            browser=browser,
            token_hash=hash_token(token),
            ext_version=ext_version,
            last_seen_at=now,
            last_used_at=now,
        )
        pairing.install_id = install.id
        await self.session.commit()
        return {
            "token": token,
            "username": user.username,
            "min_version": minimum,
            "allowed_url_prefixes": list(self.config.allowed_url_prefixes),
        }

    async def authenticate(self, token: str, version: str | None) -> ExtensionCaller:
        if not token.startswith(TOKEN_PREFIX) or not 36 <= len(token) <= 128:
            raise ServiceError("bad_token", BAD_TOKEN)
        install = await self.store.install_by_token(hash_token(token))
        if install is None:
            raise ServiceError("bad_token", BAD_TOKEN)
        now = self.now()
        idle_since = install.last_used_at or install.created_at
        if install.revoked_at is not None or idle_since < now - timedelta(days=self.config.extension_token_idle_days):
            raise ServiceError("install_revoked", "Расширение отключено: подключите его заново по коду со страницы")
        user = await self.users.get_by_id(install.user_id)
        if user is None:
            raise ServiceError("install_revoked", "Расширение отключено: подключите его заново по коду со страницы")
        if not user.is_active:
            raise ServiceError("user_blocked", "Учётная запись заблокирована")
        minimum = self.config.extension_min_version
        if not version_supported(version, minimum):
            raise ServiceError("upgrade_required", f"Обновите расширение до версии {minimum} или новее")
        install.last_seen_at = now
        install.last_used_at = now
        if version and version != install.ext_version:
            install.ext_version = version[:16]
        await self.session.commit()
        return ExtensionCaller(
            install_id=install.id, user_id=user.id, username=user.username, ext_version=install.ext_version
        )

    async def installs(self, user_id: uuid.UUID) -> list[dict[str, Any]]:
        return [
            {
                "id": item.id,
                "browser": item.browser,
                "ext_version": item.ext_version,
                "created_at": item.created_at,
                "last_seen_at": item.last_seen_at,
                "last_used_at": item.last_used_at,
            }
            for item in await self.store.live_installs(user_id)
        ]

    async def revoke(self, user_id: uuid.UUID, install_id: uuid.UUID) -> None:
        if not await self.store.revoke(user_id, install_id, self.now()):
            raise ServiceError("not_found", "Установка не найдена")
        await self.session.commit()


def _last_activity(install: ExtensionInstallModel) -> datetime:
    return install.last_seen_at or install.created_at
