import asyncio
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.fin_reports.application.build import FACTS_VERSION
from backend.modules.fin_reports.application.costs import CostFileError, match_cabinets, read_cost_file
from backend.modules.fin_reports.application.report import FinReportFile, build_workbook
from backend.modules.fin_reports.application.view import (
    CostUploadResult,
    FinReportsOverview,
    PeriodColumn,
    PnlView,
    SellerState,
    UncostedArticle,
)
from backend.modules.fin_reports.domain import (
    GRANULARITIES,
    GRANULARITY_MONTH,
    GRANULARITY_WEEK,
    MARKETPLACE_WB,
    ZERO,
    CostBook,
    CostPrice,
    Period,
    Statement,
    parse_period,
    period_bounds,
    period_of,
    statement,
)
from backend.modules.fin_reports.infrastructure.postgres import FinReportsRepository
from backend.modules.wb_core.application import SalesReportMirror
from backend.modules.wb_core.domain import SalesReport, SalesReportTotals, Seller
from backend.modules.wb_core.infrastructure.postgres import SellerRepository

# Отчёт недели на стыке лет начинается в соседнем году: окно чтения — с запасом в неделю.
YEAR_MARGIN = timedelta(days=7)
FIRST_YEAR = 2024


class FinReportsQueryError(Exception):
    """Запрос с параметрами, которых отчёт не понимает; текст — для человека."""


class FinReportsService:
    """ОПиУ подключённых кабинетов из сумм, которые воркер сложил из зеркала отчётов реализации.

    Суммы отчёта неизменяемы, а себестоимость и состав строк меняются —
    поэтому строки ОПиУ считаются при чтении из готовых сумм: быстро и без
    пересборки истории при каждой правке цены.
    """

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        repository: FinReportsRepository,
        *,
        timezone: ZoneInfo,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.repository = repository
        self.timezone = timezone
        self.mirror = SalesReportMirror(session)

    def today(self, now: datetime | None = None) -> date:
        return (now or datetime.now(UTC)).astimezone(self.timezone).date()

    async def tracked(self) -> list[Seller]:
        """Подключённые и активные: архивный кабинет в отчёт не идёт, история остаётся."""
        tracked = await self.repository.tracked_seller_ids()
        return [seller for seller in await self.sellers.list_sellers() if seller.id in tracked]

    async def overview(self) -> FinReportsOverview:
        sellers = await self.tracked()
        collected: list[datetime] = []
        failing = 0
        for seller in sellers:
            state = await self.mirror.state(seller.id)
            if state is not None and state.collected_at is not None:
                collected.append(state.collected_at)
            if state is not None and state.error:
                failing += 1
        return FinReportsOverview(len(sellers), max(collected) if collected else None, failing)

    # --- отчёт ------------------------------------------------------------------

    async def view(self, *, year: int | None, granularity: str, seller_id: uuid.UUID | None = None) -> PnlView:
        year = year or self.today().year
        if granularity not in GRANULARITIES:
            raise FinReportsQueryError("Отчёт строится по неделям или по месяцам")
        if not FIRST_YEAR <= year <= self.today().year + 1:
            raise FinReportsQueryError("За этот год отчётов нет")
        sellers = await self.tracked()
        if seller_id is not None:
            sellers = [seller for seller in sellers if seller.id == seller_id]
            if not sellers:
                raise FinReportsQueryError("Кабинет не подключён к финансовым отчётам")
        books = await self._cost_books([seller.id for seller in sellers])
        columns: dict[Period, PeriodColumn] = {}
        states: list[SellerState] = []
        uncosted: dict[tuple[uuid.UUID, int], tuple[str, Decimal]] = {}
        for seller in sellers:
            states.append(await self._collect(seller, year, granularity, books[seller.id], columns, uncosted))
        periods = [columns[period] for period in sorted(columns, reverse=True)]
        total = Statement()
        total_by_seller: dict[uuid.UUID, Statement] = {seller.id: Statement() for seller in sellers}
        for column in periods:
            total.add(column.total)
            for owner, figures in column.by_seller.items():
                total_by_seller[owner].add(figures)
        names = {seller.id: seller.name for seller in sellers}
        return PnlView(
            year=year,
            granularity=granularity,
            sellers=states,
            periods=periods,
            total=total.close(),
            total_by_seller={owner: figures.close() for owner, figures in total_by_seller.items()},
            uncosted=sorted(
                (
                    UncostedArticle(owner, names[owner], nm_id, vendor_code, revenue)
                    for (owner, nm_id), (vendor_code, revenue) in uncosted.items()
                ),
                key=lambda item: item.revenue,
                reverse=True,
            ),
        )

    async def _collect(
        self,
        seller: Seller,
        year: int,
        granularity: str,
        book: CostBook,
        columns: dict[Period, PeriodColumn],
        uncosted: dict[tuple[uuid.UUID, int], tuple[str, Decimal]],
    ) -> SellerState:
        """Отчёты кабинета за год — в общие колонки периодов."""
        since, until = date(year, 1, 1) - YEAR_MARGIN, date(year, 12, 31) + YEAR_MARGIN
        mirrored = await self.mirror.reports(seller.id, since=since, until=until)
        built = await self.repository.built_reports(seller.id)
        # Год периода считается по неделе, а не по дате: неделя с 29 декабря — уже следующий год.
        # У месяцев отчёт на стыке лет нужен обоим годам — его строки делятся по дате операции.
        in_year = [
            item
            for item in mirrored
            if year in {period_of(day, granularity).year for day in (item.report.date_from, item.report.date_to)}
        ]
        # В цифры идёт отчёт, который зеркало дочитало, а воркер уже сложил; остальные — «неполные».
        loaded = {
            item.report.report_id: item.report
            for item in in_year
            if item.loaded and built.get(item.report.report_id) == FACTS_VERSION
        }
        by_period: dict[tuple[int, Period], list[SalesReportTotals]] = defaultdict(list)
        for totals in await self.repository.facts(seller.id, since=since, until=until):
            if totals.report_id not in loaded:
                continue
            report = loaded[totals.report_id]
            # Неделя — отчёт целиком. Месяц — по дате операции WB: отчёт недели на стыке
            # месяцев WB делит не всегда, и его строки расходятся по двум месяцам.
            day = report.date_from if granularity == GRANULARITY_WEEK else (totals.month or report.date_from)
            by_period[(totals.report_id, period_of(day, granularity))].append(totals)

        def remember(item: SalesReportTotals, revenue: Decimal) -> None:
            key = (seller.id, item.nm_id)
            vendor_code, known = uncosted.get(key, ("", ZERO))
            uncosted[key] = (item.vendor_code or vendor_code, known + revenue)

        def column_of(period: Period, report: SalesReport) -> PeriodColumn | None:
            if period.year != year:
                return None
            date_from, date_to = period_bounds(period, report.date_from, report.date_to)
            column = columns.get(period)
            if column is None:
                column = columns[period] = PeriodColumn(period, date_from, date_to)
            column.date_from = min(column.date_from, date_from)
            column.date_to = max(column.date_to, date_to)
            return column

        for item in in_year:
            report = item.report
            if report.report_id in loaded:
                continue
            # Недочитанный отчёт делает неполным каждый период, которого касается.
            for day in (report.date_from, report.date_to):
                column = column_of(period_of(day, granularity), report)
                if column is not None:
                    column.pending.add(seller.id)
        for (report_id, period), items in by_period.items():
            report = loaded[report_id]
            column = column_of(period, report)
            if column is None:
                continue
            figures = statement(items, self._cost_on(book, report.date_to), on_uncosted=remember)
            column.by_seller.setdefault(seller.id, Statement()).add(figures)
            column.by_seller[seller.id].close()
            column.total.add(figures)
            column.total.close()
        state = await self.mirror.state(seller.id)
        return SellerState(
            seller_id=seller.id,
            name=seller.name,
            reports=len(in_year),
            pending_reports=len(in_year) - len(loaded),
            collected_at=state.collected_at if state else None,
            built_at=(await self.repository.last_built_at([seller.id])).get(seller.id),
            error=state.error if state else None,
        )

    @staticmethod
    def _cost_on(book: CostBook, day: date) -> Callable[[int], Decimal | None]:
        """Себестоимость артикула на конец отчёта: цена, действовавшая в тот период."""

        def cost(nm_id: int) -> Decimal | None:
            return book.cost(str(nm_id), day)

        return cost

    async def _cost_books(self, seller_ids: list[uuid.UUID]) -> dict[uuid.UUID, CostBook]:
        prices: dict[uuid.UUID, list[CostPrice]] = {seller_id: [] for seller_id in seller_ids}
        for price in await self.repository.cost_prices(seller_ids, MARKETPLACE_WB):
            prices[price.seller_id].append(price)
        return {seller_id: CostBook(items) for seller_id, items in prices.items()}

    # --- выгрузка ---------------------------------------------------------------

    async def export(self, *, year: int | None, period: str | None) -> FinReportFile:
        """Книга за год: выбранный период в разрезе кабинетов, недели, месяцы и артикулы без себестоимости."""
        weeks = await self.view(year=year, granularity=GRANULARITY_WEEK)
        months = await self.view(year=year, granularity=GRANULARITY_MONTH)
        chosen: PeriodColumn | None = None
        if period:
            try:
                wanted = parse_period(period)
            except ValueError as error:
                raise FinReportsQueryError("Период задаётся как 2026-W39 или 2026-M09") from error
            source = weeks if wanted.granularity == GRANULARITY_WEEK else months
            chosen = next((column for column in source.periods if column.period == wanted), None)
            if chosen is None:
                raise FinReportsQueryError("За этот период отчётов нет")
        elif weeks.periods:
            # Без выбора — последняя неделя, по которой дочитаны все кабинеты.
            chosen = next((column for column in weeks.periods if not column.pending), weeks.periods[0])
        return await asyncio.to_thread(build_workbook, weeks, months, chosen, self.today())

    # --- себестоимость -----------------------------------------------------------

    async def upload_costs(
        self,
        content: bytes,
        *,
        uploaded_by: uuid.UUID | None,
        effective_from: date | None = None,
        now: datetime | None = None,
    ) -> CostUploadResult:
        """Новые версии себестоимости из файла: действуют с `effective_from`, без неё — с сегодня.

        Цена, совпавшая с действующей на эту дату, версию не создаёт: файл
        загружают каждый месяц целиком, а меняется в нём десяток строк. Дата в
        прошлом — для исправления: ошибку в первой загрузке иначе не убрать,
        ведь периоды до первой версии считаются по ней.
        """
        parsed = await asyncio.to_thread(read_cost_file, content)
        today = self.today(now)
        day = effective_from or today
        if day > today:
            raise FinReportsQueryError("Себестоимость нельзя загрузить будущей датой")
        sellers = {seller.id: seller.name for seller in await self.sellers.list_sellers()}
        cabinets = match_cabinets({row.cabinet for row in parsed.rows}, sellers)
        owners = set(cabinets.values())
        known = await self.repository.cost_prices(owners, parsed.marketplace)
        books = {owner: CostBook(price for price in known if price.seller_id == owner) for owner in owners}
        # Кабинет, записанный в файле двумя способами, сводится к одному селлеру:
        # артикул остаётся один, побеждает последняя строка.
        fresh: dict[tuple[uuid.UUID, str], CostPrice] = {}
        outcome: dict[tuple[uuid.UUID, str], str] = {}
        for row in parsed.rows:
            owner = cabinets.get(row.cabinet)
            if owner is None:
                continue
            key = (owner, row.article)
            fresh.pop(key, None)
            current = books[owner].cost(row.article, day)
            if current == row.cost:
                outcome[key] = "unchanged"
                continue
            outcome[key] = "added" if current is None else "changed"
            fresh[key] = CostPrice(owner, parsed.marketplace, row.article, row.vendor_code, row.cost, day)
        await self.repository.save_cost_prices(fresh.values(), uploaded_by=uploaded_by)
        await self.session.commit()
        counts = Counter(outcome.values())
        return CostUploadResult(
            marketplace=parsed.marketplace,
            added=counts["added"],
            changed=counts["changed"],
            unchanged=counts["unchanged"],
            unknown_cabinets=sorted({row.cabinet for row in parsed.rows if row.cabinet not in cabinets}),
            problems=parsed.problems,
            effective_from=day,
        )


__all__ = ["CostFileError", "FinReportsQueryError", "FinReportsService"]
