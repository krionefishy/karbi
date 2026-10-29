import uuid

from backend.modules.platform.application.users import UserNotFoundError
from backend.modules.platform.domain import SERVICE_IDS, SERVICES, Service, User
from backend.modules.platform.infrastructure.postgres import UserRepository

ALL_SERVICE_IDS = [service.id for service in SERVICES]


class UnknownServiceError(Exception):
    def __init__(self, service_ids: list[str]) -> None:
        super().__init__(", ".join(service_ids))
        self.service_ids = service_ids


class ServiceAccessService:
    """Какие сервисы видит сотрудник. Администратору открыто всё без строк в таблице,
    иначе новый сервис оставлял бы администраторов без него."""

    def __init__(self, users: UserRepository) -> None:
        self._users = users

    @staticmethod
    def catalog() -> tuple[Service, ...]:
        return SERVICES

    async def granted(self, user: User) -> list[str]:
        if user.is_admin:
            return list(ALL_SERVICE_IDS)
        owned = await self._users.service_ids(user.id)
        return [service_id for service_id in ALL_SERVICE_IDS if service_id in owned]

    async def allows(self, user: User, service_id: str) -> bool:
        return user.is_admin or service_id in await self._users.service_ids(user.id)

    async def granted_by_user(self, users: list[User]) -> dict[uuid.UUID, list[str]]:
        owned = await self._users.service_ids_by_user()
        return {
            user.id: [s for s in ALL_SERVICE_IDS if user.is_admin or s in owned.get(user.id, set())] for user in users
        }

    async def replace(self, actor_id: uuid.UUID, user_id: uuid.UUID, service_ids: list[str]) -> User:
        unknown = sorted(set(service_ids) - SERVICE_IDS)
        if unknown:
            raise UnknownServiceError(unknown)
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError(str(user_id))
        await self._users.replace_services(user_id, set(service_ids), actor_id)
        return user
