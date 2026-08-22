import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.platform.domain import User
from backend.modules.platform.infrastructure.postgres.models import UserModel


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_username(self, username: str) -> User | None:
        result = await self._session.execute(select(UserModel).where(UserModel.username == username))
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        model = await self._session.get(UserModel, user_id)
        return self._to_domain(model) if model else None

    async def list_all(self) -> list[User]:
        result = await self._session.scalars(select(UserModel).order_by(UserModel.created_at))
        return [self._to_domain(model) for model in result]

    async def create(self, username: str, password_hash: str, is_admin: bool = False) -> User:
        model = UserModel(username=username, password_hash=password_hash, is_active=True, is_admin=is_admin)
        self._session.add(model)
        await self._session.commit()
        await self._session.refresh(model)
        return self._to_domain(model)

    async def set_active(self, user_id: uuid.UUID, is_active: bool) -> User | None:
        return await self._update(user_id, is_active=is_active)

    async def set_admin(self, user_id: uuid.UUID, is_admin: bool) -> User | None:
        return await self._update(user_id, is_admin=is_admin)

    async def set_password(self, user_id: uuid.UUID, password_hash: str) -> User | None:
        return await self._update(user_id, password_hash=password_hash)

    async def mark_login(self, user_id: uuid.UUID) -> None:
        model = await self._session.get(UserModel, user_id)
        if model is None:
            return
        model.last_login_at = datetime.now(UTC)
        await self._session.commit()

    async def _update(self, user_id: uuid.UUID, **fields: object) -> User | None:
        model = await self._session.get(UserModel, user_id)
        if model is None:
            return None
        for name, value in fields.items():
            setattr(model, name, value)
        await self._session.commit()
        await self._session.refresh(model)
        return self._to_domain(model)

    @staticmethod
    def _to_domain(model: UserModel) -> User:
        return User(
            id=model.id,
            username=model.username,
            password_hash=model.password_hash,
            is_active=model.is_active,
            is_admin=model.is_admin,
            created_at=model.created_at,
            last_login_at=model.last_login_at,
        )
