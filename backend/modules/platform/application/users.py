import secrets
import string
import uuid

from sqlalchemy.exc import IntegrityError

from backend.modules.platform.application.auth import AuthenticationError, PasswordService
from backend.modules.platform.domain import User
from backend.modules.platform.infrastructure.postgres import UserRepository

GENERATED_PASSWORD_LENGTH = 24
_ALPHABET = "".join(sorted(set(string.ascii_letters + string.digits) - set("0OoIl1")))


class UsernameTakenError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


class SelfLockoutError(Exception):
    """Blocking or demoting yourself locks the last administrator out of the panel."""


def generate_password(length: int = GENERATED_PASSWORD_LENGTH) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


class UserAdminService:
    def __init__(self, users: UserRepository, passwords: PasswordService) -> None:
        self._users = users
        self._passwords = passwords

    async def list_users(self) -> list[User]:
        return await self._users.list_all()

    async def create(self, username: str, is_admin: bool) -> tuple[User, str]:
        name = username.strip()
        if not name:
            raise ValueError("username must not be empty")
        if await self._users.get_by_username(name) is not None:
            raise UsernameTakenError(name)
        password = generate_password()
        try:
            user = await self._users.create(name, self._passwords.hash(password), is_admin=is_admin)
        except IntegrityError as error:
            raise UsernameTakenError(name) from error
        return user, password

    async def set_active(self, actor_id: uuid.UUID, user_id: uuid.UUID, is_active: bool) -> User:
        if actor_id == user_id and not is_active:
            raise SelfLockoutError
        return self._found(await self._users.set_active(user_id, is_active), user_id)

    async def set_admin(self, actor_id: uuid.UUID, user_id: uuid.UUID, is_admin: bool) -> User:
        if actor_id == user_id and not is_admin:
            raise SelfLockoutError
        return self._found(await self._users.set_admin(user_id, is_admin), user_id)

    async def reset_password(self, user_id: uuid.UUID) -> tuple[User, str]:
        password = generate_password()
        user = self._found(await self._users.set_password(user_id, self._passwords.hash(password)), user_id)
        return user, password

    async def change_own_password(self, user_id: uuid.UUID, current_password: str, new_password: str) -> None:
        user = await self._users.get_by_id(user_id)
        if user is None or not self._passwords.verify(current_password, user.password_hash):
            raise AuthenticationError
        await self._users.set_password(user_id, self._passwords.hash(new_password))

    @staticmethod
    def _found(user: User | None, user_id: uuid.UUID) -> User:
        if user is None:
            raise UserNotFoundError(str(user_id))
        return user
