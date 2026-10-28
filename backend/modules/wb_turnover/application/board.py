import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import RemainsMirror, SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_turnover.domain import compute_board_cover, cover_tone
from backend.modules.wb_turnover.infrastructure.postgres import TurnoverRepository

MOSCOW = ZoneInfo("Europe/Moscow")
WB_STOCK_WAREHOUSE = "Склад WB РФ"
BARCODE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")

FBS_YES = "yes"
FBS_NO = "no"
FBS_UNKNOWN = "unknown"


class BoardInputError(Exception):
    """Вписано то, что таблица принять не может, или строки, которой нет."""


@dataclass(frozen=True, slots=True)
class BoardRow:
    seller_id: uuid.UUID
    seller_name: str
    barcode: str
    article: str
    vendor_code: str
    name: str
    nomenclature: str
    note: str
    in_catalog: bool
    stock: int | None
    orders: int
    rate: float
    days: int | None
    status: str
    tone: str
    fbs: str


@dataclass(frozen=True, slots=True)
class HiddenBoardRow:
    seller_id: uuid.UUID
    seller_name: str
    barcode: str
    note: str
    name: str
    hidden_at: datetime


@dataclass(frozen=True, slots=True)
class BoardSeller:
    """Кабинет таблицы и то, насколько можно верить его цифрам прямо сейчас."""

    seller_id: uuid.UUID
    name: str
    remains_at: datetime | None
    remains_stale: bool
    remains_error: str | None
    fbs_at: datetime | None
    orders_without_barcode: int


@dataclass(frozen=True, slots=True)
class BoardView:
    day: date
    window_from: date
    window_to: date
    window_days: int
    red_days: int
    yellow_days: int
    sellers: list[BoardSeller]
    rows: list[BoardRow]
    hidden: list[HiddenBoardRow]


@dataclass(frozen=True, slots=True)
class AddResult:
    added: int
    existing: int


class BoardService:
    """Таблица «хватит на N дней» по списку баркодов аналитика.

    Всё считается при чтении, из уже собранного: заказы — из таблицы заказов
    автоматизации, остаток «Склад WB РФ» — из зеркала `wb_core`, наличие FBS — из
    слоя остатков по складам продавца. Окно и пороги можно менять, и таблица
    сразу считается по-новому.
    """

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        turnover: TurnoverRepository,
        *,
        window_days: int,
        red_days: int,
        yellow_days: int,
        stale_hours: int,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.turnover = turnover
        self.remains = RemainsMirror(session)
        self.window_days = window_days
        self.red_days = red_days
        self.yellow_days = yellow_days
        self.stale = timedelta(hours=stale_hours)

    async def view(self, today: date | None = None, now: datetime | None = None) -> BoardView:
        now = now or datetime.now(UTC)
        day = today or now.astimezone(MOSCOW).date()
        window_to = day - timedelta(days=1)
        window_from = day - timedelta(days=self.window_days)

        tracked = await self.turnover.tracked_seller_ids()
        active = sorted(
            (seller for seller in await self.sellers.list_sellers() if seller.id in tracked),
            key=lambda seller: seller.name.lower(),
        )
        items = await self.turnover.board_items([seller.id for seller in active])
        by_seller: dict[uuid.UUID, list] = {}
        for item in items:
            by_seller.setdefault(item.seller_id, []).append(item)

        states: list[BoardSeller] = []
        rows: list[BoardRow] = []
        hidden: list[HiddenBoardRow] = []
        for seller in active:
            seller_items = by_seller.get(seller.id, [])
            shown = [item for item in seller_items if item.hidden_at is None]
            barcodes = [item.barcode for item in shown]

            snapshot = await self.remains.snapshot(seller.id)
            stale = snapshot.collected_at is not None and snapshot.collected_at < now - self.stale
            tracked_row = await self.turnover.tracked(seller.id)
            fbs_at = tracked_row.fbs_stocks_at if tracked_row else None
            states.append(
                BoardSeller(
                    seller_id=seller.id,
                    name=seller.name,
                    remains_at=snapshot.collected_at,
                    remains_stale=stale,
                    remains_error=snapshot.error,
                    fbs_at=fbs_at,
                    orders_without_barcode=await self.turnover.orders_without_barcode(
                        seller.id, window_from, window_to
                    ),
                )
            )

            catalog = {article.article: article for article in await self.sellers.list_articles(seller.id)}
            cards = await self.sellers.list_barcode_cards(seller.id)
            from_orders = await self.turnover.barcode_articles(seller.id, barcodes)
            from_remains = {remain.barcode: remain.article for remain in snapshot.remains}
            orders = await self.turnover.orders_by_barcode(seller.id, window_from, window_to, barcodes)
            wb_stock = {
                remain.barcode: remain.quantity
                for remain in snapshot.remains
                if remain.warehouse_name == WB_STOCK_WAREHOUSE
            }
            fbs_by_article = await self.turnover.warehouse_stocks(seller.id) if fbs_at else {}

            for item in shown:
                card = cards.get(item.barcode)
                article = card.article if card else from_remains.get(item.barcode) or from_orders.get(item.barcode, "")
                known = catalog.get(article)
                stock = None if snapshot.collected_at is None else wb_stock.get(item.barcode, 0)
                cover = compute_board_cover(
                    orders=orders.get(item.barcode, 0), stock=stock, window_days=self.window_days
                )
                if fbs_at is None or not article:
                    fbs = FBS_UNKNOWN
                else:
                    fbs = FBS_YES if sum(fbs_by_article.get(article, {}).values()) > 0 else FBS_NO
                rows.append(
                    BoardRow(
                        seller_id=seller.id,
                        seller_name=seller.name,
                        barcode=item.barcode,
                        article=article,
                        vendor_code=(card.vendor_code if card else "") or (known.vendor_code if known else ""),
                        name=known.name if known else "",
                        nomenclature=item.nomenclature_1c,
                        note=item.note,
                        in_catalog=card is not None,
                        stock=stock,
                        orders=cover.orders,
                        rate=cover.rate,
                        days=cover.days,
                        status=cover.status,
                        tone=cover_tone(cover, red_days=self.red_days, yellow_days=self.yellow_days),
                        fbs=fbs,
                    )
                )
            for item in seller_items:
                if item.hidden_at is not None:
                    card = cards.get(item.barcode)
                    known = catalog.get(card.article) if card else None
                    hidden.append(
                        HiddenBoardRow(
                            seller_id=seller.id,
                            seller_name=seller.name,
                            barcode=item.barcode,
                            note=item.note,
                            name=known.name if known else "",
                            hidden_at=item.hidden_at,
                        )
                    )
        return BoardView(
            day=day,
            window_from=window_from,
            window_to=window_to,
            window_days=self.window_days,
            red_days=self.red_days,
            yellow_days=self.yellow_days,
            sellers=states,
            rows=rows,
            hidden=hidden,
        )

    async def add_items(
        self,
        seller_id: uuid.UUID,
        raw: Sequence[str],
        added_by: uuid.UUID | None,
        nomenclature: dict[str, str] | None = None,
    ) -> AddResult:
        """Вписать баркоды. Данные по ним подтянутся при ближайшем чтении, а заказы и остаток уже собраны."""
        await self._enrolled(seller_id)
        barcodes: list[str] = []
        for value in raw:
            barcode = value.strip()
            if not barcode:
                continue
            if not BARCODE.match(barcode):
                raise BoardInputError(f"«{barcode[:40]}» не похоже на баркод")
            barcodes.append(barcode)
        unique = list(dict.fromkeys(barcodes))
        added_codes = await self.turnover.add_board_items(seller_id, unique, added_by)
        for barcode, text in (nomenclature or {}).items():
            await self.turnover.update_board_item(
                seller_id, barcode, note=None, nomenclature=text.strip()[:500], updated_by=None
            )
        await self.session.commit()
        return AddResult(added=added_codes, existing=len(unique) - added_codes)

    async def update_item(
        self,
        seller_id: uuid.UUID,
        barcode: str,
        *,
        note: str | None,
        nomenclature: str | None,
        updated_by: uuid.UUID | None,
    ) -> None:
        await self._enrolled(seller_id)
        found = await self.turnover.update_board_item(
            seller_id,
            barcode,
            note=note.strip()[:2000] if note is not None else None,
            nomenclature=nomenclature.strip()[:500] if nomenclature is not None else None,
            updated_by=updated_by,
        )
        if not found:
            raise BoardInputError("Такого баркода в таблице нет")
        await self.session.commit()

    async def set_hidden(self, seller_id: uuid.UUID, barcode: str, hidden: bool) -> None:
        await self._enrolled(seller_id)
        if not await self.turnover.set_board_item_hidden(seller_id, barcode, hidden):
            raise BoardInputError("Такого баркода в таблице нет")
        await self.session.commit()

    async def remove_item(self, seller_id: uuid.UUID, barcode: str) -> None:
        await self._enrolled(seller_id)
        if not await self.turnover.delete_board_item(seller_id, barcode):
            raise BoardInputError("Такого баркода в таблице нет")
        await self.session.commit()

    async def request_refresh_all(self, requested_by: uuid.UUID | None) -> int:
        """Поставить сбор заказов по всем кабинетам, где есть строки. Остаток WB придёт из зеркала сам."""
        tracked = await self.turnover.tracked_seller_ids()
        active = {seller.id for seller in await self.sellers.list_sellers()}
        wanted = {item.seller_id for item in await self.turnover.board_items() if item.hidden_at is None}
        queued = 0
        for seller_id in sorted(wanted & tracked & active, key=str):
            await self.turnover.request_refresh(seller_id, requested_by)
            queued += 1
        await self.session.commit()
        return queued

    async def _enrolled(self, seller_id: uuid.UUID) -> None:
        seller = await self.sellers.get(seller_id)
        if seller is None or seller.archived_at is not None:
            raise SellerNotFoundError
        if seller_id not in await self.turnover.tracked_seller_ids():
            raise SellerNotFoundError
