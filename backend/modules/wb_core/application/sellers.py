import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.domain import Article, Seller
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.shared.kafka_streams.topics import WBCoreTopics
from backend.shared.outbox import OutboxRepository
from backend.shared.security import CredentialCipher


class SellerNotFoundError(Exception):
    pass


class DuplicateCredentialError(Exception):
    pass


class SellerService:
    def __init__(self, session: AsyncSession, repository: SellerRepository, cipher: CredentialCipher) -> None:
        self.session = session
        self.repository = repository
        self.cipher = cipher

    async def list_sellers(self) -> list[Seller]:
        return await self.repository.list_sellers()

    async def create(self, name: str, api_key: str) -> Seller:
        fingerprint = self.cipher.fingerprint(api_key)
        if await self.repository.fingerprint_exists(fingerprint):
            raise DuplicateCredentialError
        seller = await self.repository.create(name.strip(), self.cipher.encrypt(api_key), fingerprint)
        self._queue_sync(seller.id, "seller_created")
        await self.session.commit()
        return Seller(seller.id, seller.name, 0, "queued", None, None)

    async def update(self, seller_id: uuid.UUID, name: str | None, api_key: str | None) -> Seller:
        seller = await self.repository.get(seller_id)
        if seller is None:
            raise SellerNotFoundError
        if name is not None:
            seller.name = name.strip()
        if api_key is not None:
            fingerprint = self.cipher.fingerprint(api_key)
            if await self.repository.fingerprint_exists(fingerprint, excluding=seller_id):
                raise DuplicateCredentialError
            credential = await self.repository.get_credential(seller_id)
            if credential is None:
                raise SellerNotFoundError
            credential.encrypted_api_key = self.cipher.encrypt(api_key)
            credential.key_fingerprint = fingerprint
            seller.catalog_sync_status = "queued"
            seller.catalog_sync_error = None
            self._queue_sync(seller.id, "credential_updated")
        await self.session.commit()
        listed = {item.id: item for item in await self.repository.list_sellers()}
        return listed[seller_id]

    async def delete(self, seller_id: uuid.UUID) -> None:
        if not await self.repository.delete(seller_id):
            raise SellerNotFoundError
        await self.session.commit()

    async def request_sync(self, seller_id: uuid.UUID) -> Seller:
        seller = await self.repository.get(seller_id)
        if seller is None:
            raise SellerNotFoundError
        seller.catalog_sync_status = "queued"
        seller.catalog_sync_error = None
        self._queue_sync(seller.id, "manual_retry")
        await self.session.commit()
        listed = {item.id: item for item in await self.repository.list_sellers()}
        return listed[seller_id]

    async def articles(self, seller_id: uuid.UUID) -> list[Article]:
        if await self.repository.get(seller_id) is None:
            raise SellerNotFoundError
        return await self.repository.list_articles(seller_id)

    def _queue_sync(self, seller_id: uuid.UUID, reason: str) -> None:
        OutboxRepository(self.session).add(
            aggregate_id=seller_id,
            event_type="WBCatalogSyncRequested",
            topic=WBCoreTopics.CATALOG_SYNC_REQUESTED,
            payload={
                "seller_id": str(seller_id),
                "reason": reason,
                "requested_at": datetime.now(UTC).isoformat(),
                "schema_version": 1,
            },
        )
