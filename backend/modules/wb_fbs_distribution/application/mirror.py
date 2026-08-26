import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.modules.wb_fbs_distribution.infrastructure.postgres import FbsDistributionRepository
from backend.modules.wb_fbs_distribution.infrastructure.wb import WBFbsMarketplaceClient
from backend.shared.security import CredentialCipher, CredentialDecryptionError


@dataclass(frozen=True, slots=True)
class MirrorResult:
    """Что принесла одна сверка кабинета."""

    offices: int
    warehouses: int


class MirrorService:
    """Сверка справочника объектов WB и виртуальных складов кабинета.

    Читает и записывает только зеркало. Ничего в кабинете не меняет: пока
    оператор не собрал целевой набор складов, автоматизации нечего создавать,
    а фоновая сверка не тот процесс, которому такое право стоит выдавать.
    """

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        distribution: FbsDistributionRepository,
        marketplace: WBFbsMarketplaceClient,
        cipher: CredentialCipher,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.distribution = distribution
        self.marketplace = marketplace
        self.cipher = cipher

    async def sync_seller(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> MirrorResult:
        """Обновить справочник объектов и склады одного кабинета.

        Справочник объектов у всех кабинетов один, но спросить его можно только
        ключом: сверка кабинета попутно освежает общий каталог.
        """
        api_key = await self._api_key(seller_id)
        # Сеть впереди: держать транзакцию открытой через два запроса к WB
        # значит держать и её блокировки.
        await self.session.commit()

        offices = await self.marketplace.offices(api_key)
        warehouses = await self.marketplace.warehouses(api_key)

        stamp = now or datetime.now(UTC)
        if not await self.distribution.tracked(seller_id):
            # Пока шли запросы, кабинет отключили: запись сейчас воскресила бы
            # то, что удалило отключение.
            return MirrorResult(offices=0, warehouses=0)
        await self.distribution.replace_offices(offices, now=stamp)
        await self.distribution.replace_warehouses(seller_id, warehouses, now=stamp)
        await self.session.commit()
        return MirrorResult(offices=len(offices), warehouses=len(warehouses))

    async def _api_key(self, seller_id: uuid.UUID) -> str:
        credential = await self.sellers.get_credential(seller_id)
        if credential is None:
            raise WBPermanentError("У селлера нет API-ключа")
        try:
            return self.cipher.decrypt(credential.encrypted_api_key)
        except CredentialDecryptionError as error:
            raise WBPermanentError("API-ключ селлера не расшифровывается") from error
