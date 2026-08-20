import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application.enrollment import AutomationEnrollment
from backend.modules.wb_core.domain import Article, Seller
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.shared.kafka_streams.topics import WBCoreTopics
from backend.shared.outbox import OutboxRepository
from backend.shared.security import CredentialCipher


class SellerNotFoundError(Exception):
    pass


class DuplicateCredentialError(Exception):
    pass


class SellerArchivedError(Exception):
    """The seller is out of service, so nothing may be collected for him."""


class AutomationNotFoundError(Exception):
    pass


class SellerService:
    """The seller registry: who exists, and which automations he is connected to.

    Sellers live here and only here. An automation enrolls and releases them
    through :class:`AutomationEnrollment`, so leaving an automation never
    reaches the registry, and the registry never touches an automation's tables.
    """

    def __init__(
        self,
        session: AsyncSession,
        repository: SellerRepository,
        cipher: CredentialCipher,
        enrollments: Sequence[AutomationEnrollment] = (),
    ) -> None:
        self.session = session
        self.repository = repository
        self.cipher = cipher
        self.enrollments = tuple(enrollments)

    async def list_sellers(self, *, include_archived: bool = False) -> list[Seller]:
        return await self.repository.list_sellers(include_archived=include_archived)

    async def create(self, name: str, api_key: str) -> Seller:
        fingerprint = self.cipher.fingerprint(api_key)
        if await self.repository.fingerprint_exists(fingerprint):
            raise DuplicateCredentialError
        try:
            seller = await self.repository.create(name.strip(), self.cipher.encrypt(api_key), fingerprint)
            self._queue_sync(seller.id, "seller_created")
            await self.session.commit()
        except IntegrityError as error:
            # A concurrent insert slipped in between the fingerprint check and the commit.
            await self.session.rollback()
            raise DuplicateCredentialError from error
        return Seller(seller.id, seller.name, 0, "queued", None, None)

    async def update(self, seller_id: uuid.UUID, name: str | None, api_key: str | None) -> Seller:
        seller = await self._active(seller_id)
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
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise DuplicateCredentialError from error
        return await self._reload(seller_id)

    async def archive(self, seller_id: uuid.UUID) -> Seller:
        """Retire the seller: he leaves every automation, collected data stays."""
        await self._active(seller_id)
        for enrollment in self.enrollments:
            await enrollment.detach(seller_id)
        await self.repository.archive(seller_id)
        await self.session.commit()
        return await self._reload(seller_id, include_archived=True)

    async def restore(self, seller_id: uuid.UUID, api_key: str) -> Seller:
        """Bring an archived seller back. The key is asked for again — archiving dropped it."""
        seller = await self.repository.get(seller_id)
        if seller is None:
            raise SellerNotFoundError
        if seller.archived_at is None:
            return await self._reload(seller_id)
        fingerprint = self.cipher.fingerprint(api_key)
        if await self.repository.fingerprint_exists(fingerprint, excluding=seller_id):
            raise DuplicateCredentialError
        await self.repository.restore(seller_id, self.cipher.encrypt(api_key), fingerprint)
        self._queue_sync(seller_id, "seller_restored")
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise DuplicateCredentialError from error
        return await self._reload(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        """Delete the seller for good, together with what every automation collected."""
        if await self.repository.get(seller_id) is None:
            raise SellerNotFoundError
        for enrollment in self.enrollments:
            await enrollment.purge(seller_id)
        await self.repository.delete(seller_id)
        await self.session.commit()

    async def request_sync(self, seller_id: uuid.UUID) -> Seller:
        seller = await self._active(seller_id)
        seller.catalog_sync_status = "queued"
        seller.catalog_sync_error = None
        self._queue_sync(seller.id, "manual_retry")
        await self.session.commit()
        return await self._reload(seller_id)

    async def articles(self, seller_id: uuid.UUID) -> list[Article]:
        if await self.repository.get(seller_id) is None:
            raise SellerNotFoundError
        return await self.repository.list_articles(seller_id)

    async def automations_of(self, seller_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
        """Which automations each seller belongs to, in one pass per automation."""
        wanted = set(seller_ids)
        found: dict[uuid.UUID, list[str]] = {seller_id: [] for seller_id in wanted}
        for enrollment in self.enrollments:
            for seller_id in await enrollment.seller_ids():
                if seller_id in wanted:
                    found[seller_id].append(enrollment.automation_id)
        return found

    def enrollment(self, automation_id: str) -> AutomationEnrollment:
        for enrollment in self.enrollments:
            if enrollment.automation_id == automation_id:
                return enrollment
        raise AutomationNotFoundError

    async def enrolled(self, automation_id: str) -> list[Seller]:
        enrolled = await self.enrollment(automation_id).seller_ids()
        return [seller for seller in await self.repository.list_sellers() if seller.id in enrolled]

    async def enroll(self, automation_id: str, seller_id: uuid.UUID) -> Seller:
        enrollment = self.enrollment(automation_id)
        await self._active(seller_id)
        await enrollment.attach(seller_id)
        await self.session.commit()
        return await self._reload(seller_id)

    async def unenroll(self, automation_id: str, seller_id: uuid.UUID) -> None:
        """Disconnect from one automation. The seller and his data both stay."""
        enrollment = self.enrollment(automation_id)
        if await self.repository.get(seller_id) is None:
            raise SellerNotFoundError
        await enrollment.detach(seller_id)
        await self.session.commit()

    async def _active(self, seller_id: uuid.UUID):
        seller = await self.repository.get(seller_id)
        if seller is None:
            raise SellerNotFoundError
        if seller.archived_at is not None:
            raise SellerArchivedError
        return seller

    async def _reload(self, seller_id: uuid.UUID, *, include_archived: bool = False) -> Seller:
        listed = {seller.id: seller for seller in await self.repository.list_sellers(include_archived=include_archived)}
        if seller_id not in listed:
            raise SellerNotFoundError
        return listed[seller_id]

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
