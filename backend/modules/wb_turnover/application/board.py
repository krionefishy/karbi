import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_turnover.application.board_import import BoardFileError, normalize, parse_list
from backend.modules.wb_turnover.domain import compute_board_cover, cover_tone
from backend.modules.wb_turnover.infrastructure.postgres import TurnoverRepository

MOSCOW = ZoneInfo("Europe/Moscow")
BARCODE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")

FBS_YES = "yes"
FBS_NO = "no"
FBS_UNKNOWN = "unknown"


class BoardInputError(Exception):
    """Вписано то, что таблица принять не может, или строки, которой нет."""


def warehouse_key(name: str) -> str:
    return " ".join(name.lower().split())


@dataclass(frozen=True, slots=True)
class FbsWarehouseOption:
    key: str
    label: str
    selected: bool
    cabinets: int


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
    stock_at: datetime | None
    stock_stale: bool
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


@dataclass(frozen=True, slots=True)
class ImportCabinet:
    """Что вышло с одним кабинетом файла: нашёлся ли, подключили ли, сколько строк добавлено."""

    name: str
    seller_name: str | None
    connected_now: bool
    added: int
    existing: int
    unknown: int


@dataclass(frozen=True, slots=True)
class ImportResult:
    cabinets: list[ImportCabinet]


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
        self.window_days = window_days
        self.red_days = red_days
        self.yellow_days = yellow_days
        self.stale = timedelta(hours=stale_hours)

    async def fbs_warehouses(self) -> list[FbsWarehouseOption]:
        """Все склады кабинетов с отметкой, учитывается ли склад в «Остатках ФБС»."""
        chosen = await self.turnover.board_fbs_warehouse_labels()
        found: dict[str, FbsWarehouseOption] = {}
        tracked = await self.turnover.tracked_seller_ids()
        for seller in await self.sellers.list_sellers():
            if seller.id not in tracked:
                continue
            for _, name in await self.turnover.warehouses(seller.id):
                key = warehouse_key(name)
                option = found.get(key)
                if option is None:
                    found[key] = FbsWarehouseOption(
                        key=key, label=" ".join(name.split()), selected=key in chosen, cabinets=1
                    )
                else:
                    found[key] = replace(option, cabinets=option.cabinets + 1)
        for key, label in chosen.items():
            found.setdefault(key, FbsWarehouseOption(key=key, label=label, selected=True, cabinets=0))
        return sorted(found.values(), key=lambda option: (not option.selected, option.label.lower()))

    async def set_fbs_warehouses(self, keys: list[str]) -> list[FbsWarehouseOption]:
        known = {option.key: option.label for option in await self.fbs_warehouses()}
        chosen = {key: known[key] for key in dict.fromkeys(keys) if key in known}
        await self.turnover.set_board_fbs_warehouses(chosen)
        await self.session.commit()
        return await self.fbs_warehouses()

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

        chosen_warehouses = await self.turnover.board_fbs_warehouse_keys()
        states: list[BoardSeller] = []
        rows: list[BoardRow] = []
        hidden: list[HiddenBoardRow] = []
        for seller in active:
            seller_items = by_seller.get(seller.id, [])
            shown = [item for item in seller_items if item.hidden_at is None]
            barcodes = [item.barcode for item in shown]

            tracked_row = await self.turnover.tracked(seller.id)
            stock_at = tracked_row.fbo_stocks_at if tracked_row else None
            fbs_at = tracked_row.fbs_stocks_at if tracked_row else None
            states.append(
                BoardSeller(
                    seller_id=seller.id,
                    name=seller.name,
                    stock_at=stock_at,
                    stock_stale=stock_at is not None and stock_at < now - self.stale,
                    fbs_at=fbs_at,
                    orders_without_barcode=await self.turnover.orders_without_barcode(
                        seller.id, window_from, window_to
                    ),
                )
            )

            catalog = {article.article: article for article in await self.sellers.list_articles(seller.id)}
            cards = await self.sellers.list_barcode_cards(seller.id)
            from_orders = await self.turnover.barcode_articles(seller.id, barcodes)
            orders = await self.turnover.orders_by_barcode(seller.id, window_from, window_to, barcodes)
            sizes = await self.sellers.list_barcode_sizes(seller.id)
            fbo_by_size = await self.turnover.fbo_stocks(seller.id) if stock_at else {}
            fbs_by_article = await self.turnover.warehouse_stocks(seller.id) if fbs_at else {}
            chosen_ids = {
                warehouse_id
                for warehouse_id, name in await self.turnover.warehouses(seller.id)
                if warehouse_key(name) in chosen_warehouses
            }

            for item in shown:
                card = cards.get(item.barcode)
                article = card.article if card else from_orders.get(item.barcode, "")
                known = catalog.get(article)
                size = sizes.get(item.barcode)
                stock = fbo_by_size.get(size[0], 0) if stock_at and size else None
                cover = compute_board_cover(
                    orders=orders.get(item.barcode, 0), stock=stock, window_days=self.window_days
                )
                if fbs_at is None or not article:
                    fbs = FBS_UNKNOWN
                else:
                    on_shelf = sum(
                        quantity
                        for warehouse_id, quantity in fbs_by_article.get(article, {}).items()
                        if warehouse_id in chosen_ids
                    )
                    fbs = FBS_YES if on_shelf > 0 else FBS_NO
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

    async def import_list(self, content: bytes, requested_by: uuid.UUID | None) -> ImportResult:
        """Загрузить список из Excel: подключить кабинеты файла, вписать баркоды, поставить сбор.

        Повторная загрузка ничего не задваивает — новые баркоды добавляются, прежние
        остаются со своими комментариями. Кабинет, которого нет в реестре, пропускается
        и попадает в ответ без добавленных строк. Кабинет из файла подключается к
        «Оборачиваемости» сам: файл и есть решение, что за ним следим.
        """
        try:
            rows = parse_list(content)
        except BoardFileError as error:
            raise BoardInputError(str(error)) from error
        if not rows:
            raise BoardInputError("В файле нет строк с кабинетом и баркодом")
        registry = await self.sellers.list_sellers()
        tracked = await self.turnover.tracked_seller_ids()
        results: list[ImportCabinet] = []
        for name in dict.fromkeys(row.seller for row in rows):
            wanted = list(dict.fromkeys(row.barcode for row in rows if row.seller == name))
            seller = next((item for item in registry if normalize(item.name) == normalize(name)), None)
            if seller is None:
                results.append(ImportCabinet(name, None, False, 0, 0, 0))
                continue
            connected_now = seller.id not in tracked
            if connected_now:
                await self.turnover.track(seller.id)
            barcodes = [row.barcode for row in rows if row.seller == name]
            for barcode in barcodes:
                if not BARCODE.match(barcode):
                    raise BoardInputError(f"«{barcode[:40]}» не похоже на баркод")
            added = await self.turnover.add_board_items(seller.id, wanted, requested_by)
            for row in rows:
                if row.seller == name and row.nomenclature:
                    await self.turnover.update_board_item(
                        seller.id, row.barcode, note=None, nomenclature=row.nomenclature[:500], updated_by=None
                    )
            known = await self.sellers.list_barcode_sizes(seller.id)
            await self.turnover.request_refresh(seller.id, requested_by)
            results.append(
                ImportCabinet(
                    name=name,
                    seller_name=seller.name,
                    connected_now=connected_now,
                    added=added,
                    existing=len(wanted) - added,
                    unknown=sum(1 for barcode in wanted if barcode not in known),
                )
            )
        await self.session.commit()
        return ImportResult(results)

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
