import contextlib
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application.enrollment import AutomationEnrollment
from backend.modules.wb_core.domain import Article, Seller
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import EgressAdminError, EgressGateway
from backend.shared.kafka_streams.topics import WBCoreTopics
from backend.shared.outbox import OutboxRepository

# Статусы селлера на шлюзе, какими их видит админка. Первая группа приходит от
# самого шлюза; вторая описывает доставку с нашей стороны.
EGRESS_UNDELIVERED = "undelivered"  # шлюз недоступен, ключ не доехал
EGRESS_UNSYNCED = "unsynced"  # локальная правка есть, шлюз о ней не знает


class SellerNotFoundError(Exception):
    pass


class DuplicateCredentialError(Exception):
    """Исторический тип: ключи больше не хранятся здесь, дубликаты не ловятся."""


class SellerArchivedError(Exception):
    """The seller is out of service, so nothing may be collected for him."""


class AutomationNotFoundError(Exception):
    pass


class SellerService:
    """The seller registry: who exists, and which automations he is connected to.

    Sellers live here and only here. Ключ селлера в базу не пишется вовсе: из
    запроса регистрации он синхронно уезжает на шлюз wb-egress и существует
    только там. Исход доставки — статус на селлере (сага из WB_EGRESS.md):
    шлюз отвечает `delivered`/`verified`/`key_invalid`/`no_free_ip`, а если он
    недоступен — селлер остаётся в `undelivered`, и ключ нужно ввести ещё раз.
    """

    def __init__(
        self,
        session: AsyncSession,
        repository: SellerRepository,
        gateway: EgressGateway,
        enrollments: Sequence[AutomationEnrollment] = (),
    ) -> None:
        self.session = session
        self.repository = repository
        self.gateway = gateway
        self.enrollments = tuple(enrollments)

    async def list_sellers(self, *, include_archived: bool = False) -> list[Seller]:
        return await self.repository.list_sellers(include_archived=include_archived)

    async def create(self, name: str, api_key: str) -> Seller:
        seller = await self.repository.create(name.strip())
        delivered = await self._deliver_key(seller.id, seller.name, api_key)
        if delivered:
            self._queue_sync(seller.id, "seller_created")
        await self.session.commit()
        return await self._reload(seller.id)

    async def update(self, seller_id: uuid.UUID, name: str | None, api_key: str | None) -> Seller:
        seller = await self._active(seller_id)
        if name is not None:
            seller.name = name.strip()
        if api_key is not None:
            # Новый ключ едет на шлюз вместе с актуальным именем одним вызовом.
            seller.catalog_sync_status = "queued"
            seller.catalog_sync_error = None
            if await self._deliver_key(seller.id, seller.name, api_key):
                self._queue_sync(seller.id, "credential_updated")
        elif name is not None:
            await self._rename_on_egress(seller.id, seller.name)
        await self.session.commit()
        return await self._reload(seller_id)

    async def archive(self, seller_id: uuid.UUID) -> Seller:
        """Retire the seller: he leaves every automation, collected data stays.

        На шлюзе селлер отключается, но его IP остаётся закреплённым: вернувшись,
        он не засветится в WB со второго адреса.
        """
        await self._active(seller_id)
        for enrollment in self.enrollments:
            await enrollment.detach(seller_id)
        await self.repository.archive(seller_id)
        try:
            await self.gateway.disable_seller(seller_id=str(seller_id), event_version=self._version())
            await self.repository.set_egress_state(seller_id, status="disabled", error=None)
        except EgressAdminError as error:
            await self.repository.set_egress_state(seller_id, status=EGRESS_UNSYNCED, error=str(error))
        await self.session.commit()
        return await self._reload(seller_id, include_archived=True)

    async def restore(self, seller_id: uuid.UUID, api_key: str) -> Seller:
        """Bring an archived seller back. The key is asked for again — archiving dropped it."""
        seller = await self.repository.get(seller_id)
        if seller is None:
            raise SellerNotFoundError
        if seller.archived_at is None:
            return await self._reload(seller_id)
        await self.repository.restore(seller_id)
        if await self._deliver_key(seller_id, seller.name, api_key):
            self._queue_sync(seller_id, "seller_restored")
        await self.session.commit()
        return await self._reload(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        """Delete the seller for good, together with what every automation collected."""
        if await self.repository.get(seller_id) is None:
            raise SellerNotFoundError
        for enrollment in self.enrollments:
            await enrollment.purge(seller_id)
        await self.repository.delete(seller_id)
        # Строки селлера уже нет — статус писать некуда; недоставленное
        # отключение добьёт сверка (backend/commands/sync_egress_status.py).
        with contextlib.suppress(EgressAdminError):
            await self.gateway.disable_seller(seller_id=str(seller_id), event_version=self._version())
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

    async def _deliver_key(self, seller_id: uuid.UUID, name: str, api_key: str) -> bool:
        """Отдать ключ шлюзу и записать исход. True — по ключу можно работать."""
        try:
            outcome = await self.gateway.put_seller(
                seller_id=str(seller_id), name=name, api_key=api_key, event_version=self._version()
            )
        except EgressAdminError as error:
            await self.repository.set_egress_state(seller_id, status=EGRESS_UNDELIVERED, error=str(error))
            return False
        status = str(outcome.get("status") or EGRESS_UNDELIVERED)
        await self.repository.set_egress_state(
            seller_id,
            status=status,
            error=str(outcome.get("verify_error") or "") or None,
            ip=outcome.get("egress_ip"),
        )
        return status in {"verified", "delivered"}

    async def _rename_on_egress(self, seller_id: uuid.UUID, name: str) -> None:
        try:
            await self.gateway.rename_seller(seller_id=str(seller_id), name=name, event_version=self._version())
        except EgressAdminError as error:
            await self.repository.set_egress_state(seller_id, status=EGRESS_UNSYNCED, error=str(error))

    @staticmethod
    def _version() -> int:
        """Монотонная версия события для идемпотентных upsert'ов шлюза."""
        return time.time_ns() // 1_000_000

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
