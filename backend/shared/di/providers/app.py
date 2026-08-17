from collections.abc import AsyncIterator

from dishka import Provider, Scope, from_context, provide
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.platform.application import AuthService, PasswordService, TokenService
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.shared.kafka_streams.producer import KafkaProducerWrapper
from backend.shared.settings import Settings
from backend.storage.pg import Database
from backend.storage.redis import RedisClient


class AppProvider(Provider):
    scope = Scope.APP

    settings = from_context(Settings)
    database = from_context(Database)
    redis = from_context(RedisClient)
    kafka_producer = from_context(KafkaProducerWrapper)
    token_service = from_context(TokenService)

    @provide(scope=Scope.APP)
    def password_service(self) -> PasswordService:
        return PasswordService()


class WorkerProvider(Provider):
    scope = Scope.APP

    settings = from_context(Settings)
    database = from_context(Database)


class SessionProvider(Provider):
    @provide(scope=Scope.REQUEST)
    async def session(self, database: Database) -> AsyncIterator[AsyncSession]:
        async with database.session() as session:
            yield session

    @provide(scope=Scope.REQUEST)
    def user_repository(self, session: AsyncSession) -> UserRepository:
        return UserRepository(session)

    @provide(scope=Scope.REQUEST)
    def auth_service(
        self,
        users: UserRepository,
        passwords: PasswordService,
        tokens: TokenService,
    ) -> AuthService:
        return AuthService(users, passwords, tokens)
