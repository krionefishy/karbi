from collections.abc import AsyncIterator

from dishka import Provider, Scope, from_context, provide
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotRegistry, SubscriptionService
from backend.modules.notifications.infrastructure.postgres import NotificationRepository
from backend.modules.platform.application import AuthService, PasswordService, TokenService, UserAdminService
from backend.modules.platform.infrastructure.postgres import UserRepository
from backend.modules.wb_core.application import AutomationEnrollment, SellerService
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_reviews.application import ReviewsEnrollment, ReviewSyncService
from backend.modules.wb_reviews.infrastructure.postgres import ReviewSyncRepository
from backend.modules.wb_turnover.application import TurnoverEnrollment, TurnoverService
from backend.modules.wb_turnover.infrastructure.postgres import TurnoverRepository
from backend.shared.kafka_streams.producer import KafkaProducerWrapper
from backend.shared.security import CredentialCipher
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

    @provide(scope=Scope.APP)
    def credential_cipher(self, settings: Settings) -> CredentialCipher:
        return CredentialCipher(
            settings.security.credential_encryption_keys, settings.security.credential_fingerprint_key
        )


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

    @provide(scope=Scope.REQUEST)
    def user_admin_service(self, users: UserRepository, passwords: PasswordService) -> UserAdminService:
        return UserAdminService(users, passwords)

    @provide(scope=Scope.REQUEST)
    def seller_repository(self, session: AsyncSession) -> SellerRepository:
        return SellerRepository(session)

    @provide(scope=Scope.REQUEST)
    def seller_service(
        self,
        session: AsyncSession,
        repository: SellerRepository,
        cipher: CredentialCipher,
        enrollments: list[AutomationEnrollment],
    ) -> SellerService:
        return SellerService(session, repository, cipher, enrollments)

    @provide(scope=Scope.REQUEST)
    def review_sync_repository(self, session: AsyncSession) -> ReviewSyncRepository:
        return ReviewSyncRepository(session)

    @provide(scope=Scope.REQUEST)
    def reviews_enrollment(self, reviews: ReviewSyncRepository) -> ReviewsEnrollment:
        return ReviewsEnrollment(reviews)

    @provide(scope=Scope.REQUEST)
    def turnover_repository(self, session: AsyncSession) -> TurnoverRepository:
        return TurnoverRepository(session)

    @provide(scope=Scope.REQUEST)
    def turnover_enrollment(self, turnover: TurnoverRepository) -> TurnoverEnrollment:
        return TurnoverEnrollment(turnover)

    @provide(scope=Scope.REQUEST)
    def notification_repository(self, session: AsyncSession) -> NotificationRepository:
        return NotificationRepository(session)

    @provide(scope=Scope.REQUEST)
    def bot_registry(
        self, session: AsyncSession, notifications: NotificationRepository, cipher: CredentialCipher
    ) -> BotRegistry:
        return BotRegistry(session, notifications, cipher)

    @provide(scope=Scope.REQUEST)
    def subscription_service(
        self, session: AsyncSession, notifications: NotificationRepository, settings: Settings
    ) -> SubscriptionService:
        return SubscriptionService(session, notifications, invite_ttl_hours=settings.telegram.invite_link_ttl_hours)

    @provide(scope=Scope.REQUEST)
    def turnover_service(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        turnover: TurnoverRepository,
        bots: BotRegistry,
        subscriptions: SubscriptionService,
        settings: Settings,
    ) -> TurnoverService:
        return TurnoverService(
            session,
            sellers,
            turnover,
            bots,
            subscriptions,
            bot_code=settings.turnover.notification_bot,
        )

    @provide(scope=Scope.REQUEST)
    def automation_enrollments(
        self, reviews: ReviewsEnrollment, turnover: TurnoverEnrollment
    ) -> list[AutomationEnrollment]:
        """Every automation a seller can be connected to. New module — new line here."""
        return [reviews, turnover]

    @provide(scope=Scope.REQUEST)
    def review_sync_service(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        reviews: ReviewSyncRepository,
    ) -> ReviewSyncService:
        return ReviewSyncService(session, sellers, reviews)
