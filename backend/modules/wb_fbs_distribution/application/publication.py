import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBPermanentError, WBTemporaryError
from backend.modules.wb_fbs_distribution.application.warehouses import WriteNotAllowedError
from backend.modules.wb_fbs_distribution.infrastructure.postgres import FbsDistributionRepository
from backend.modules.wb_fbs_distribution.infrastructure.wb import WBFbsMarketplaceClient, WBFbsStockWriter

VERIFIED = "verified"
DRIFT = "drift"
FAILED = "failed"


class NothingToPublishError(Exception):
    """Публиковать нечего: плана нет."""


@dataclass(frozen=True, slots=True)
class WarehouseOutcome:
    warehouse_id: int
    sent: int
    drift: int
    status: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class PublicationResult:
    plan_id: uuid.UUID
    outcomes: list[WarehouseOutcome] = field(default_factory=list)

    @property
    def sent(self) -> int:
        return sum(outcome.sent for outcome in self.outcomes)

    @property
    def drift(self) -> int:
        return sum(outcome.drift for outcome in self.outcomes)

    @property
    def failed(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status == FAILED)


class PublicationService:
    """Привести остатки WB к последнему плану.

    Отправляются только изменившиеся пары «склад + баркод»: WB незачем получать
    те же числа заново, а бюджет запросов общий с остальными модулями.
    """

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        distribution: FbsDistributionRepository,
        marketplace: WBFbsMarketplaceClient,
        writer: WBFbsStockWriter,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.distribution = distribution
        self.marketplace = marketplace
        self.writer = writer

    async def publish(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> PublicationResult:
        stamp = now or datetime.now(UTC)
        seller_key = await self._writable_seller(seller_id)

        plan = await self.distribution.latest_plan(seller_id)
        if plan is None:
            raise NothingToPublishError("Для кабинета ещё не считался план")

        desired = await self._desired(seller_id, plan.id)
        published = await self.distribution.published(seller_id)
        # Пара, которая была опубликована, но в плане её больше нет, обязана
        # уехать нулём: иначе проданный или выведенный товар останется в WB.
        changes: dict[int, dict[str, int]] = {}
        for key in set(desired) | set(published):
            amount = desired.get(key, 0)
            if published.get(key) != amount:
                changes.setdefault(key[0], {})[key[1]] = amount
        await self.session.commit()

        outcomes = []
        for warehouse_id, amounts in sorted(changes.items()):
            outcomes.append(await self._publish_one(seller_key, seller_id, plan.id, warehouse_id, amounts, stamp))
        return PublicationResult(plan_id=plan.id, outcomes=outcomes)

    async def _publish_one(
        self,
        seller_key: str,
        seller_id: uuid.UUID,
        plan_id: uuid.UUID,
        warehouse_id: int,
        amounts: dict[str, int],
        stamp: datetime,
    ) -> WarehouseOutcome:
        rows = sorted(amounts.items())
        try:
            await self.writer.publish(seller_key, warehouse_id, rows)
        except (WBPermanentError, WBTemporaryError) as error:
            await self._record(seller_id, plan_id, warehouse_id, len(rows), FAILED, 0, str(error), stamp)
            return WarehouseOutcome(warehouse_id, sent=0, drift=0, status=FAILED, error=str(error))

        # Ответ 204 не значит, что число опубликовано: WB не валидирует имена
        # полей и на неверном имени вернёт успех, ничего не изменив. Поэтому
        # состояние считается подтверждённым только после вычитки.
        try:
            actual = await self.marketplace.stocks(seller_key, warehouse_id, [sku for sku, _ in rows])
        except (WBPermanentError, WBTemporaryError) as error:
            await self._record(seller_id, plan_id, warehouse_id, len(rows), FAILED, 0, str(error), stamp)
            return WarehouseOutcome(warehouse_id, sent=len(rows), drift=0, status=FAILED, error=str(error))

        # Отсутствующая строка — это ноль: WB не возвращает позиции без остатка.
        confirmed = {sku: actual.get(sku, 0) for sku, _ in rows}
        drift = sum(1 for sku, amount in rows if confirmed[sku] != amount)
        await self.distribution.confirm_published(seller_id, warehouse_id, confirmed, now=stamp)
        status = DRIFT if drift else VERIFIED
        await self._record(seller_id, plan_id, warehouse_id, len(rows), status, drift, None, stamp)
        await self.session.commit()
        return WarehouseOutcome(warehouse_id, sent=len(rows), drift=drift, status=status)

    async def _record(
        self,
        seller_id: uuid.UUID,
        plan_id: uuid.UUID,
        warehouse_id: int,
        rows: int,
        status: str,
        drift: int,
        error: str | None,
        stamp: datetime,
    ) -> None:
        await self.distribution.record_publication(
            seller_id=seller_id,
            plan_id=plan_id,
            warehouse_id=warehouse_id,
            created_at=stamp,
            rows=rows,
            status=status,
            drift=drift,
            error=error,
        )
        await self.session.commit()

    async def _desired(self, seller_id: uuid.UUID, plan_id: uuid.UUID) -> dict[tuple[int, str], int]:
        """Чего план хочет от WB, ключом «склад + баркод».

        Остатки WB пишутся по `sku`, а план считается по размерам, поэтому
        баркод берётся из связи, которой размер и был найден. Размер без
        баркода не публикуется: писать его некуда.
        """
        barcodes = {
            mapping.chrt_id: mapping.barcode
            for mapping in await self.distribution.mappings(seller_id)
            if mapping.barcode
        }
        desired: dict[tuple[int, str], int] = {}
        for item in await self.distribution.plan_items(plan_id):
            sku = barcodes.get(item.chrt_id)
            if sku is None:
                continue
            desired[(item.warehouse_id, sku)] = item.amount
        return desired

    async def _writable_seller(self, seller_id: uuid.UUID) -> str:
        enrollment = await self.distribution.enrollment(seller_id)
        if enrollment is None:
            raise SellerNotFoundError(str(seller_id))
        if not enrollment.write_enabled:
            raise WriteNotAllowedError("Кабинету не разрешена запись в Wildberries")
        # Ключа здесь больше нет: шлюз подставит его сам по seller_id.
        return str(seller_id)
