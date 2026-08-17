from collections.abc import AsyncIterator

from dishka import Provider, Scope, from_context, provide
from sqlalchemy.ext.asyncio import AsyncSession

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


class WorkerProvider(Provider):
    scope = Scope.APP

    settings = from_context(Settings)
    database = from_context(Database)


class SessionProvider(Provider):
    @provide(scope=Scope.REQUEST)
    async def session(self, database: Database) -> AsyncIterator[AsyncSession]:
        async with database.session() as session:
            yield session
