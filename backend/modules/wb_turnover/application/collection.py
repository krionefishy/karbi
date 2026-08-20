import logging
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import WBPermanentError
from backend.modules.wb_turnover.infrastructure.postgres import TurnoverRepository
from backend.modules.wb_turnover.infrastructure.wb import WBMarketplaceClient, WBStatisticsClient
from backend.shared.security import CredentialCipher, CredentialDecryptionError

# Wildberries reports and accepts statistics dates in Moscow time without a zone.
MOSCOW = ZoneInfo("Europe/Moscow")
WAREHOUSE_REFRESH = timedelta(days=1)


class CollectionService:
    """Fills the two series the metric divides: stock now, orders over the window."""

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        turnover: TurnoverRepository,
        cipher: CredentialCipher,
        statistics: WBStatisticsClient,
        marketplace: WBMarketplaceClient,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.turnover = turnover
        self.cipher = cipher
        self.statistics = statistics
        self.marketplace = marketplace
        self.logger = logging.getLogger("wb.turnover.collection")

    async def collect_stocks(self, seller_id: uuid.UUID, snapshot_date: date, slot: int) -> int:
        """One stock snapshot: FBO from statistics, FBS from the seller's own warehouses."""
        api_key = await self._api_key(seller_id)
        known = await self._known_articles(seller_id)

        fbo: dict[str, list[int]] = {article: [0, 0, 0, 0] for article in known}
        for row in await self.statistics.stocks(api_key):
            values = fbo.setdefault(row.article, [0, 0, 0, 0])
            values[0] += row.quantity
            values[1] += row.quantity_full
            values[2] += row.in_way_to_client
            values[3] += row.in_way_from_client
        # Zero rows for everything we know: WB drops an article from the stock
        # response once it runs out, and without them the last non-zero snapshot
        # would look like the current stock forever.
        await self.turnover.upsert_snapshots(
            seller_id,
            snapshot_date,
            slot,
            "fbo",
            {article: (values[0], values[1], values[2], values[3]) for article, values in fbo.items()},
        )

        fbs = await self._collect_fbs(seller_id, api_key)
        if fbs is not None:
            await self.turnover.upsert_snapshots(seller_id, snapshot_date, slot, "fbs", fbs)
        await self.session.commit()
        return len(fbo)

    async def _collect_fbs(self, seller_id: uuid.UUID, api_key: str) -> dict[str, tuple[int, int, int, int]] | None:
        """Declared stock at the seller's warehouses, or None when he has none."""
        warehouses = await self._warehouses(seller_id, api_key)
        if not warehouses:
            return None
        barcodes = await self.sellers.list_barcodes(seller_id)
        if not barcodes:
            return None
        article_of = {barcode: article for article, skus in barcodes.items() for barcode in skus}
        amounts: dict[str, int] = defaultdict(int)
        for warehouse_id, _ in warehouses:
            declared = await self.marketplace.stocks(api_key, warehouse_id, list(article_of))
            for barcode, amount in declared.items():
                article = article_of.get(barcode)
                if article is not None:
                    amounts[article] += amount
        return {article: (amounts.get(article, 0), amounts.get(article, 0), 0, 0) for article in barcodes}

    async def _warehouses(self, seller_id: uuid.UUID, api_key: str) -> list[tuple[int, str]]:
        tracked = await self.turnover.tracked(seller_id)
        synced_at = tracked.warehouses_synced_at if tracked else None
        if synced_at is not None and datetime.now(UTC) - synced_at < WAREHOUSE_REFRESH:
            return await self.turnover.warehouses(seller_id)
        warehouses = [(item.id, item.name) for item in await self.marketplace.warehouses(api_key)]
        await self.turnover.replace_warehouses(seller_id, warehouses)
        return warehouses

    async def collect_orders(
        self, seller_id: uuid.UUID, now: datetime, *, backfill_days: int, overlap_hours: int
    ) -> int:
        """Pull orders changed since the watermark and move it forward.

        The watermark only advances on success, so a failed run repeats the same
        window instead of leaving a hole, and the overlap covers orders that
        changed while the previous pull was running.
        """
        api_key = await self._api_key(seller_id)
        tracked = await self.turnover.tracked(seller_id)
        watermark = tracked.orders_watermark if tracked else None
        if watermark is None:
            # A seller connected today still gets a full window: unlike stock,
            # order history is served on request.
            date_from = now - timedelta(days=backfill_days)
        else:
            date_from = watermark - timedelta(hours=overlap_hours)

        rows = await self.statistics.orders(api_key, date_from.astimezone(MOSCOW))
        if rows:
            await self.turnover.upsert_orders(
                seller_id,
                [
                    {
                        "srid": row.srid,
                        "article": row.article,
                        "order_date": row.order_date,
                        "last_change_date": self._aware(row.last_change_date),
                        "is_cancel": row.is_cancel,
                        "price": row.price,
                        "warehouse_type": row.warehouse_type,
                    }
                    for row in rows
                ],
            )
            await self.turnover.set_watermark(seller_id, max(self._aware(row.last_change_date) for row in rows))
        await self.session.commit()
        return len(rows)

    @staticmethod
    def _aware(moment: datetime) -> datetime:
        """WB sends Moscow time without a zone; storing it as UTC would shift it three hours."""
        return moment if moment.tzinfo is not None else moment.replace(tzinfo=MOSCOW)

    async def _known_articles(self, seller_id: uuid.UUID) -> set[str]:
        return {
            article.article for article in await self.sellers.list_articles(seller_id) if article.state != "archived"
        }

    async def _api_key(self, seller_id: uuid.UUID) -> str:
        credential = await self.sellers.get_credential(seller_id)
        if credential is None:
            raise WBPermanentError("У селлера нет API-ключа")
        try:
            return self.cipher.decrypt(credential.encrypted_api_key)
        except CredentialDecryptionError as error:
            raise WBPermanentError("API-ключ селлера не расшифровывается") from error
