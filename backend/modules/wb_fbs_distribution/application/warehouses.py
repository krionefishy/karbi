import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.modules.wb_fbs_distribution.application.mirror import MirrorService
from backend.modules.wb_fbs_distribution.infrastructure.postgres import FbsDistributionRepository
from backend.modules.wb_fbs_distribution.infrastructure.wb import WBFbsMarketplaceClient, WBFbsWarehouseWriter
from backend.shared.security import CredentialCipher


class WriteNotAllowedError(Exception):
    """У кабинета не включено право менять его в WB."""


class WarehouseConflictError(Exception):
    """Команду выполнять нельзя: она сломала бы кабинет."""


@dataclass(frozen=True, slots=True)
class CreatedWarehouse:
    warehouse_id: int
    office_id: int
    name: str


class WarehouseAdminService:
    """Создание, перепривязка и удаление виртуальных складов кабинета.

    Каждая команда — отдельное действие оператора. Фоновая сверка сюда не
    ходит: склад, пропавший из локального целевого списка, не повод удалять его
    в живом кабинете.
    """

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        distribution: FbsDistributionRepository,
        marketplace: WBFbsMarketplaceClient,
        writer: WBFbsWarehouseWriter,
        cipher: CredentialCipher,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.distribution = distribution
        self.marketplace = marketplace
        self.writer = writer
        self.cipher = cipher
        self.mirror = MirrorService(session, sellers, distribution, marketplace, cipher)

    async def create(self, seller_id: uuid.UUID, office_id: int, name: str) -> CreatedWarehouse:
        """Создать склад под объектом WB и сразу обновить зеркало.

        По одному складу за вызов, а не пачкой на весь целевой набор: WB не
        документирует общий максимум складов продавца, и упереться в него на
        сороковом из шестидесяти лучше с сорока созданными, чем с непонятной
        ошибкой посреди цикла.
        """
        api_key = await self._writable_key(seller_id)
        existing = {warehouse.office_id for warehouse in await self.distribution.warehouses(seller_id)}
        if office_id in existing:
            # WB не даёт привязать один объект к двум складам кабинета, и его
            # отказ ничего не объяснит оператору.
            raise WarehouseConflictError("Под этот объект у кабинета уже есть склад")
        if not name.strip():
            raise WarehouseConflictError("У склада должно быть название")
        await self.session.commit()

        warehouse_id = await self.writer.create(api_key, name=name.strip(), office_id=office_id)
        await self.mirror.sync_seller(seller_id)
        return CreatedWarehouse(warehouse_id=warehouse_id, office_id=office_id, name=name.strip())

    async def rebind(self, seller_id: uuid.UUID, warehouse_id: int, *, name: str, office_id: int) -> None:
        """Переименовать склад или перепривязать его к другому объекту WB."""
        api_key = await self._writable_key(seller_id)
        warehouses = {row.warehouse_id: row for row in await self.distribution.warehouses(seller_id)}
        if warehouse_id not in warehouses:
            raise WarehouseConflictError("Склад не найден в этом кабинете")
        taken = {row.office_id for row in warehouses.values() if row.warehouse_id != warehouse_id}
        if office_id in taken:
            raise WarehouseConflictError("Под этот объект у кабинета уже есть другой склад")
        await self.session.commit()

        await self.writer.rename(api_key, warehouse_id, name=name.strip(), office_id=office_id)
        await self.mirror.sync_seller(seller_id)

    async def delete(self, seller_id: uuid.UUID, warehouse_id: int) -> None:
        """Удалить склад. Необратимо, поэтому только по явной команде оператора.

        Склад, который ещё участвует в распределении, не удаляем: сначала его
        надо вывести из схемы, чтобы расчёт перестал на него рассчитывать, а на
        WB уехали нули.
        """
        api_key = await self._writable_key(seller_id)
        warehouses = {row.warehouse_id: row for row in await self.distribution.warehouses(seller_id)}
        warehouse = warehouses.get(warehouse_id)
        if warehouse is None:
            raise WarehouseConflictError("Склад не найден в этом кабинете")
        if warehouse.participates:
            raise WarehouseConflictError(
                "Склад ещё участвует в распределении: выключите участие и дождитесь публикации нулей"
            )
        await self.session.commit()

        await self.writer.delete(api_key, warehouse_id)
        await self.mirror.sync_seller(seller_id)

    async def _writable_key(self, seller_id: uuid.UUID) -> str:
        """Ключ кабинета, но только если ему разрешено менять WB."""
        enrollment = await self.distribution.enrollment(seller_id)
        if enrollment is None:
            raise SellerNotFoundError(str(seller_id))
        if not enrollment.write_enabled:
            raise WriteNotAllowedError("Кабинету не разрешена запись в Wildberries")
        credential = await self.sellers.get_credential(seller_id)
        if credential is None:
            raise WBPermanentError("У селлера нет API-ключа")
        return self.cipher.decrypt(credential.encrypted_api_key)
