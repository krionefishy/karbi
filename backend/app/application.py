from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dishka import make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI

from backend.app.api.router import router
from backend.app.http.setup import setup_http_middleware
from backend.infrastructure.logging import configure_logging
from backend.modules.platform.application import TokenService
from backend.shared.di import ALL_PROVIDERS
from backend.shared.kafka_streams.kafka import start_kafka, stop_kafka
from backend.shared.kafka_streams.producer import KafkaProducerWrapper
from backend.shared.settings import Settings, load_settings
from backend.storage.pg import Database
from backend.storage.redis import RedisClient
from backend.storage.s3 import S3Client


class Application:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        configure_logging(self.settings.app.log_level)

        self.database = Database()
        self.redis = RedisClient()
        self.token_service = TokenService(self.settings, self.redis)
        self.kafka_producer = KafkaProducerWrapper()
        self.s3 = self._create_s3_client()

        self.app = FastAPI(title="Marketplace Auto API", version="0.1.0", lifespan=self.lifespan)
        self.app.state.database = self.database
        self.app.state.redis = self.redis
        self.container = make_async_container(
            *ALL_PROVIDERS,
            context={
                Settings: self.settings,
                Database: self.database,
                RedisClient: self.redis,
                TokenService: self.token_service,
                KafkaProducerWrapper: self.kafka_producer,
            },
        )
        setup_dishka(self.container, self.app)
        self._setup_routes()
        self._setup_middleware()

    def _create_s3_client(self) -> S3Client | None:
        if not self.settings.s3.enabled:
            return None
        return S3Client(
            endpoint_url=self.settings.s3.endpoint_url,
            tenant_id=self.settings.s3.tenant_id,
            key_id=self.settings.s3.key_id,
            secret_key=self.settings.s3.secret_key,
            bucket_name=self.settings.s3.bucket,
            region=self.settings.s3.region,
            verify_tls=self.settings.s3.verify_tls,
        )

    def _setup_routes(self) -> None:
        self.app.include_router(router, prefix="/api/v1")

    def _setup_middleware(self) -> None:
        setup_http_middleware(
            self.app,
            settings=self.settings,
            redis=self.redis,
            token_service=self.token_service,
        )

    async def _on_startup(self) -> None:
        self.settings.validate_runtime_secrets()
        await self.database.connect(
            self.settings.database.url,
            echo=self.settings.database.echo,
            pool_size=self.settings.database.pool_size,
            max_overflow=self.settings.database.max_overflow,
        )
        await self.redis.connect(self.settings.redis.url)
        if self.s3 is not None:
            await self.s3.verify_connection()
        if self.settings.kafka.enabled:
            await start_kafka(
                app=self.app,
                bootstrap_servers=self.settings.kafka.bootstrap_servers,
                consumer_group=self.settings.kafka.consumer_group,
                max_request_size=self.settings.kafka.max_request_size,
                topic_partitions=self.settings.kafka.topic_partitions,
                topic_replication_factor=self.settings.kafka.topic_replication_factor,
                producer=self.kafka_producer,
                s3_client=self.s3,
            )

    async def _on_shutdown(self) -> None:
        await stop_kafka(self.app)
        await self.container.close()
        await self.redis.disconnect()
        await self.database.disconnect()

    @asynccontextmanager
    async def lifespan(self, _: FastAPI) -> AsyncIterator[None]:
        try:
            await self._on_startup()
            yield
        finally:
            await self._on_shutdown()

    def get_app(self) -> FastAPI:
        return self.app


def create_app(settings: Settings | None = None) -> FastAPI:
    return Application(settings).get_app()
