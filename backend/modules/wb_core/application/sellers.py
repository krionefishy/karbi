import logging
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application.enrollment import AutomationEnrollment
from backend.modules.wb_core.domain import MARKETPLACE_OZON, MARKETPLACE_WB, Article, Seller
from backend.modules.wb_core.domain.entities import (
    EGRESS_DISABLED,
    EGRESS_SERVABLE,
    EGRESS_UNDELIVERED,
    EGRESS_UNSYNCED,
)
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import EgressAdminError, EgressGateway
from backend.shared.kafka_streams.topics import WBCoreTopics
from backend.shared.outbox import OutboxRepository


class SellerNotFoundError(Exception):
    pass


class DuplicateCredentialError(Exception):
    """Шлюз отказал: этот ключ уже закреплён за другим селлером (и другим IP)."""


class SellerArchivedError(Exception):
    """The seller is out of service, so nothing may be collected for him."""


class AutomationNotFoundError(Exception):
    pass


class SellerService:
    """The seller registry: who exists, and which automations he is connected to.

    Sellers live here and only here. Ключ селлера в базу не пишется вовсе: из
    запроса регистрации он синхронно уезжает на шлюз wb-egress и существует
    только там. Исход доставки — статус на селлере (сага из WB_EGRESS.md).

    Правило про сеть то же, что в mirror.py: транзакция закрывается ДО похода
    на шлюз. Локальная правка коммитится первой, исход доставки — отдельной
    короткой транзакцией; держать блокировки строк через сетевой вызов с
    таймаутом до 200 секунд значило бы держать и весь пул соединений.
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
        self.logger = logging.getLogger("wb.sellers.service")

    async def list_sellers(self, *, include_archived: bool = False) -> list[Seller]:
        return await self.repository.list_sellers(include_archived=include_archived)

    async def create(self, name: str, api_key: str) -> Seller:
        seller = await self.repository.create(name.strip())
        await self.session.commit()
        await self._deliver_key(seller.id, seller.name, api_key, sync_reason="seller_created")
        return await self._reload(seller.id)

    async def update(self, seller_id: uuid.UUID, name: str | None, api_key: str | None) -> Seller:
        seller = await self._active(seller_id)
        new_name = seller.name
        if name is not None:
            seller.name = name.strip()
            new_name = seller.name
        await self.session.commit()
        if api_key is not None:
            # Новый ключ едет на шлюз вместе с актуальным именем одним вызовом.
            await self._deliver_key(seller_id, new_name, api_key, sync_reason="credential_updated")
        elif name is not None:
            await self._rename_on_egress(seller_id, new_name)
        return await self._reload(seller_id)

    async def set_ozon_credentials(
        self,
        seller_id: uuid.UUID,
        *,
        client_id: str,
        api_key: str,
        performance_client_id: str = "",
        performance_client_secret: str = "",
    ) -> Seller:
        """Завести или обновить учётку Ozon у существующего селлера.

        Отдельный вызов, а не поле в форме селлера: учётки маркетплейсов
        независимы, и ротация ключа Ozon не должна требовать ввода ключа WB.
        """
        seller = await self._active(seller_id)
        name = seller.name
        await self.session.commit()
        await self._deliver_ozon(
            seller_id,
            name,
            client_id=client_id,
            api_key=api_key,
            performance_client_id=performance_client_id,
            performance_client_secret=performance_client_secret,
        )
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
        await self.session.commit()
        await self._disable_on_egress(seller_id)
        return await self._reload(seller_id, include_archived=True)

    async def restore(self, seller_id: uuid.UUID, api_key: str) -> Seller:
        """Bring an archived seller back. The key is asked for again — archiving dropped it."""
        seller = await self.repository.get(seller_id)
        if seller is None:
            raise SellerNotFoundError
        if seller.archived_at is None:
            return await self._reload(seller_id)
        name = seller.name
        await self.repository.restore(seller_id)
        await self.session.commit()
        await self._deliver_key(seller_id, name, api_key, sync_reason="seller_restored")
        return await self._reload(seller_id)

    async def purge(self, seller_id: uuid.UUID) -> None:
        """Delete the seller for good, together with what every automation collected."""
        if await self.repository.get(seller_id) is None:
            raise SellerNotFoundError
        for enrollment in self.enrollments:
            await enrollment.purge(seller_id)
        await self.repository.delete(seller_id)
        await self.session.commit()
        try:
            await self.gateway.disable_seller(seller_id=str(seller_id), event_version=self._clock_version())
        except EgressAdminError as error:
            # Строки селлера уже нет — статус писать некуда. Громко в лог:
            # ключ остаётся живым на шлюзе, пока его не отключит сверка
            # (backend/commands/sync_egress_status.py), которая гасит
            # осиротевшие записи шлюза.
            self.logger.error("seller_purge_disable_failed", extra={"seller_id": str(seller_id), "error": str(error)})

    async def refresh_egress(self, seller_id: uuid.UUID) -> Seller:
        """Повторная проверка ключа на шлюзе — для key_invalid после починки прав в кабинете WB."""
        if await self.repository.get(seller_id) is None:
            raise SellerNotFoundError
        try:
            outcome = await self.gateway.verify_seller(str(seller_id))
        except EgressAdminError as error:
            await self.repository.set_egress_state(seller_id, status=EGRESS_UNDELIVERED, error=str(error))
            await self.session.commit()
            return await self._reload(seller_id, include_archived=True)
        await self._apply_outcome(seller_id, outcome, sync_reason=None, version=None)
        return await self._reload(seller_id, include_archived=True)

    async def refresh_ozon_egress(self, seller_id: uuid.UUID) -> Seller:
        """Повторная проверка учётки Ozon — после перевыпуска ключа в кабинете."""
        if await self.repository.get(seller_id) is None:
            raise SellerNotFoundError
        try:
            outcome = await self.gateway.verify_ozon(str(seller_id))
        except EgressAdminError as error:
            await self.repository.set_egress_state(
                seller_id, status=EGRESS_UNDELIVERED, error=str(error), marketplace=MARKETPLACE_OZON
            )
            await self.session.commit()
            return await self._reload(seller_id, include_archived=True)
        await self._apply_outcome(seller_id, outcome, sync_reason=None, version=None, marketplace=MARKETPLACE_OZON)
        return await self._reload(seller_id, include_archived=True)

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

    async def _deliver_key(self, seller_id: uuid.UUID, name: str, api_key: str, *, sync_reason: str) -> None:
        """Отдать ключ шлюзу и записать исход отдельной короткой транзакцией."""
        version = await self._next_version(seller_id)
        try:
            outcome = await self.gateway.put_seller(
                seller_id=str(seller_id), name=name, api_key=api_key, event_version=version
            )
        except EgressAdminError as error:
            if error.status_code == 409:
                # Один токен на двух селлерах означал бы один токен с двух IP.
                await self.repository.set_egress_state(seller_id, status=EGRESS_UNDELIVERED, error=str(error))
                await self.session.commit()
                raise DuplicateCredentialError(str(error)) from error
            await self.repository.set_egress_state(seller_id, status=EGRESS_UNDELIVERED, error=str(error))
            await self.session.commit()
            return
        await self._apply_outcome(seller_id, outcome, sync_reason=sync_reason, version=version)

    async def _deliver_ozon(
        self,
        seller_id: uuid.UUID,
        name: str,
        *,
        client_id: str,
        api_key: str,
        performance_client_id: str,
        performance_client_secret: str,
    ) -> None:
        """Отдать учётку Ozon шлюзу и записать исход отдельной короткой транзакцией."""
        version = await self._next_version(seller_id)
        try:
            outcome = await self.gateway.put_ozon_credentials(
                seller_id=str(seller_id),
                name=name,
                client_id=client_id,
                api_key=api_key,
                performance_client_id=performance_client_id,
                performance_client_secret=performance_client_secret,
                event_version=version,
            )
        except EgressAdminError as error:
            await self.repository.set_egress_state(
                seller_id, status=EGRESS_UNDELIVERED, error=str(error), marketplace=MARKETPLACE_OZON
            )
            await self.session.commit()
            if error.status_code == 409:
                # Один кабинет Ozon на двух селлерах означал бы один кабинет
                # с двух исходящих адресов.
                raise DuplicateCredentialError(str(error)) from error
            return
        await self._apply_outcome(seller_id, outcome, sync_reason=None, version=version, marketplace=MARKETPLACE_OZON)

    async def _apply_outcome(
        self,
        seller_id: uuid.UUID,
        outcome: dict,
        *,
        sync_reason: str | None,
        version: int | None,
        marketplace: str = MARKETPLACE_WB,
    ) -> None:
        status, error = _outcome_of(outcome, marketplace)
        await self.repository.set_egress_state(
            seller_id,
            status=status,
            error=error,
            ip=outcome.get("egress_ip"),
            version=version,
            marketplace=marketplace,
        )
        if status in EGRESS_SERVABLE and sync_reason is not None:
            await self.repository.reset_catalog_sync(seller_id)
            self._queue_sync(seller_id, sync_reason)
        await self.session.commit()

    async def _rename_on_egress(self, seller_id: uuid.UUID, name: str) -> None:
        version = await self._next_version(seller_id)
        try:
            outcome = await self.gateway.rename_seller(seller_id=str(seller_id), name=name, event_version=version)
        except EgressAdminError as error:
            await self.repository.set_egress_state(seller_id, status=EGRESS_UNSYNCED, error=str(error))
            await self.session.commit()
            return
        # Успех возвращает актуальное состояние шлюза и снимает возможный
        # прежний unsynced.
        await self._apply_outcome(seller_id, outcome, sync_reason=None, version=version)

    async def _disable_on_egress(self, seller_id: uuid.UUID) -> None:
        """Отключение селлера гасит все его учётки: шлюз делает это одним вызовом."""
        version = await self._next_version(seller_id)
        try:
            await self.gateway.disable_seller(seller_id=str(seller_id), event_version=version)
        except EgressAdminError as error:
            for marketplace in (MARKETPLACE_WB, MARKETPLACE_OZON):
                await self.repository.set_egress_state(
                    seller_id, status=EGRESS_UNSYNCED, error=str(error), marketplace=marketplace
                )
            await self.session.commit()
            return
        for marketplace in (MARKETPLACE_WB, MARKETPLACE_OZON):
            await self.repository.set_egress_state(
                seller_id, status=EGRESS_DISABLED, error=None, version=version, marketplace=marketplace
            )
        await self.session.commit()

    async def _next_version(self, seller_id: uuid.UUID) -> int:
        """max(wall-clock мс, прошлая версия + 1): монотонна и при скачке NTP назад."""
        stored = await self.repository.get_egress_version(seller_id)
        return max(self._clock_version(), stored + 1)

    @staticmethod
    def _clock_version() -> int:
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


def _outcome_of(outcome: dict, marketplace: str) -> tuple[str, str | None]:
    """Статус и ошибка по одному маркетплейсу из ответа шлюза.

    Шлюз до появления Ozon отдавал один плоский `status`, и это был статус WB.
    Разбираем и его: во время выкатки одна сторона обновляется раньше другой,
    и сага WB не должна на это время ослепнуть.
    """
    reported = outcome.get("marketplaces")
    if isinstance(reported, dict):
        entry = reported.get(marketplace)
        if isinstance(entry, dict):
            return str(entry.get("status") or EGRESS_UNDELIVERED), str(entry.get("verify_error") or "") or None
    if marketplace == MARKETPLACE_WB:
        return str(outcome.get("status") or EGRESS_UNDELIVERED), str(outcome.get("verify_error") or "") or None
    return EGRESS_UNDELIVERED, None
