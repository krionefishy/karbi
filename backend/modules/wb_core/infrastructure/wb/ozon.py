import asyncio
import logging
from collections.abc import Callable, Iterable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.modules.wb_core.domain import (
    OZON_LINE_CONTAINER,
    OZON_LINE_DELIVERY,
    OZON_LINE_ITEM,
    OZON_LINE_NON_ITEM,
    OZON_LINE_SALE,
    OzonAccrualLine,
    OzonAdvertCampaign,
    OzonAdvertStat,
    OzonProduct,
)
from backend.modules.wb_core.infrastructure.wb.client import WBPermanentError, WBTemporaryError
from backend.modules.wb_core.infrastructure.wb.egress import EgressGateway

OZON_SELLER_API = "seller"
OZON_PERFORMANCE_API = "performance"
MAX_PAGES = 200
# Карточек на страницу списка и в запросе подробностей — потолки Ozon.
PRODUCTS_PAGE = 1000
# Performance: кампаний в одном отчёте и дней в окне — потолки Ozon; отчёт готовится
# асинхронно, готовность опрашивается раз в несколько секунд.
STATS_CAMPAIGNS_CHUNK = 10
STATS_MAX_WINDOW_DAYS = 62
STATS_POLL_SECONDS = 3.0
STATS_POLL_ATTEMPTS = 200
# Реферальные кампании (блогеры, VK) статистики по SKU не имеют — отчёт по ним не просят.
STATS_SKIPPED_OBJECT_TYPES = frozenset({"REF_BLOGGER", "REF_VK"})
KOPECK = Decimal("0.01")
ZERO = Decimal("0.00")


def _money(value: Any) -> Decimal:
    """Суммы Ozon — строки внутри объекта `{amount, currency}`; пустой объект — ноль."""
    if isinstance(value, dict):
        value = value.get("amount")
    if isinstance(value, bool) or value is None:
        return ZERO
    try:
        return Decimal(str(value)).quantize(KOPECK)
    except (InvalidOperation, ValueError):
        return ZERO


def _count(value: Any) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _listing(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


class OzonJsonClient:
    """Общий вход для клиентов Ozon: тот же шлюз, что у WB, свой маршрут и свои ключи.

    Почти всё у Ozon вызывается POST с параметрами в теле, даже чтение.
    """

    api = OZON_SELLER_API
    api_name = "Ozon Seller API"

    def __init__(self, gateway: EgressGateway, *, priority: str = "background") -> None:
        self.gateway = gateway
        self.priority = priority
        self.logger = logging.getLogger(f"ozon.{self.api}.client")

    async def request(
        self, method: str, path: str, seller_id: str, *, json: Any | None = None, params: dict[str, Any] | None = None
    ) -> Any:
        return await self.gateway.call_ozon(
            seller_id=seller_id,
            api=self.api,
            method=method,
            path=path,
            params=params,
            json=json,
            priority=self.priority,
            api_name=self.api_name,
        )


class OzonFinanceClient(OzonJsonClient):
    """Начисления за день — `/v1/finance/accrual/by-day`, страницами по `last_id`."""

    async def accruals(self, seller_id: str, day: date) -> list[OzonAccrualLine]:
        lines: list[OzonAccrualLine] = []
        last_id = ""
        for _ in range(MAX_PAGES):
            payload = await self.request(
                "POST", "/v1/finance/accrual/by-day", seller_id, json={"date": day.isoformat(), "last_id": last_id}
            )
            if not isinstance(payload, dict):
                raise WBPermanentError(f"{self.api_name}: начисления за день пришли не объектом")
            for raw in _listing(payload.get("accruals")):
                if isinstance(raw, dict):
                    lines.extend(self._lines(raw, day))
            next_id = str(payload.get("last_id") or "")
            if not next_id or next_id == last_id:
                return lines
            last_id = next_id
        raise WBPermanentError(f"{self.api_name}: начисления за {day} не кончились за {MAX_PAGES} страниц")

    @staticmethod
    def _lines(raw: dict[str, Any], day: date) -> list[OzonAccrualLine]:
        accrual_id = raw.get("accrual_id")
        if not isinstance(accrual_id, int):
            return []
        posting = _mapping(raw.get("posting"))
        category = str(raw.get("accrued_category") or "")
        unit_number = str(raw.get("unit_number") or "")
        delivery_schema = str(posting.get("delivery_schema") or "")
        lines: list[OzonAccrualLine] = []

        def add(
            line: str,
            *,
            sku: int = 0,
            type_id: int = 0,
            quantity: int = 0,
            amount: Decimal = ZERO,
            sale_amount: Decimal = ZERO,
            sale_price: Decimal = ZERO,
            sale_commission: Decimal = ZERO,
            bonus: Decimal = ZERO,
            coinvestment: Decimal = ZERO,
        ) -> None:
            lines.append(
                OzonAccrualLine(
                    accrual_id=accrual_id,
                    line_no=len(lines),
                    day=day,
                    category=category,
                    unit_number=unit_number,
                    delivery_schema=delivery_schema,
                    line=line,
                    sku=sku,
                    type_id=type_id,
                    quantity=quantity,
                    amount=amount,
                    sale_amount=sale_amount,
                    sale_price=sale_price,
                    sale_commission=sale_commission,
                    bonus=bonus,
                    coinvestment=coinvestment,
                )
            )

        for product in _listing(posting.get("products")):
            product = _mapping(product)
            sku, quantity = _count(product.get("sku")), _count(product.get("quantity"))
            commission = _mapping(product.get("commission"))
            if commission:
                add(
                    OZON_LINE_SALE,
                    sku=sku,
                    quantity=quantity,
                    amount=_money(commission.get("sale_commission")),
                    sale_amount=_money(commission.get("sale_amount")),
                    sale_price=_money(commission.get("sale_price")),
                    sale_commission=_money(commission.get("sale_commission")),
                    bonus=_money(commission.get("bonus")),
                    coinvestment=_money(commission.get("coinvestment")),
                )
            for service in _listing(_mapping(product.get("delivery")).get("services")):
                service = _mapping(service)
                add(
                    OZON_LINE_DELIVERY,
                    sku=sku,
                    type_id=_count(service.get("type_id")),
                    quantity=quantity,
                    amount=_money(service.get("accrued")),
                )
        for group in _listing(_mapping(raw.get("item_fees")).get("fees")):
            group = _mapping(group)
            for fee in _listing(group.get("fees")):
                fee = _mapping(fee)
                add(
                    OZON_LINE_ITEM,
                    sku=_count(group.get("sku")),
                    type_id=_count(fee.get("type_id")),
                    quantity=_count(group.get("quantity")),
                    amount=_money(fee.get("accrued")),
                )
        for fee in _listing(_mapping(raw.get("container_fees")).get("fees")):
            fee = _mapping(fee)
            add(OZON_LINE_CONTAINER, type_id=_count(fee.get("type_id")), amount=_money(fee.get("accrued")))
        non_item = _mapping(raw.get("non_item_fee"))
        if non_item:
            add(OZON_LINE_NON_ITEM, type_id=_count(non_item.get("type_id")), amount=_money(non_item.get("accrued")))
        return lines


class OzonProductClient(OzonJsonClient):
    """Карточки кабинета — `/v3/product/list` страницами, названия — из `/v3/product/info/list`."""

    api_name = "Ozon Seller API (карточки)"

    async def products(self, seller_id: str, *, heartbeat: Callable[[], None] | None = None) -> list[OzonProduct]:
        found: dict[int, OzonProduct] = {}
        product_ids: list[int] = []
        last_id = ""
        for _ in range(MAX_PAGES):
            if heartbeat is not None:
                heartbeat()
            payload = await self.request(
                "POST",
                "/v3/product/list",
                seller_id,
                json={"filter": {"visibility": "ALL"}, "last_id": last_id, "limit": PRODUCTS_PAGE},
            )
            result = _mapping(_mapping(payload).get("result"))
            items = _listing(result.get("items"))
            for raw in items:
                raw = _mapping(raw)
                sku, product_id = _count(raw.get("sku")), _count(raw.get("product_id"))
                if not sku:
                    continue
                found[sku] = OzonProduct(
                    sku=sku,
                    product_id=product_id,
                    offer_id=str(raw.get("offer_id") or ""),
                    name="",
                    archived=bool(raw.get("archived")),
                )
                product_ids.append(product_id)
            next_id = str(result.get("last_id") or "")
            if not items or not next_id or next_id == last_id:
                break
            last_id = next_id
        else:
            raise WBPermanentError(f"{self.api_name}: список карточек не кончился за {MAX_PAGES} страниц")
        names = await self._names(seller_id, product_ids, heartbeat)
        return [
            OzonProduct(item.sku, item.product_id, item.offer_id, names.get(item.sku, ""), item.archived)
            for item in found.values()
        ]

    async def _names(
        self, seller_id: str, product_ids: list[int], heartbeat: Callable[[], None] | None
    ) -> dict[int, str]:
        names: dict[int, str] = {}
        for offset in range(0, len(product_ids), PRODUCTS_PAGE):
            if heartbeat is not None:
                heartbeat()
            payload = await self.request(
                "POST",
                "/v3/product/info/list",
                seller_id,
                json={"product_id": product_ids[offset : offset + PRODUCTS_PAGE]},
            )
            for raw in _listing(_mapping(payload).get("items")):
                raw = _mapping(raw)
                name = str(raw.get("name") or "")
                for source in _listing(raw.get("sources")):
                    sku = _count(_mapping(source).get("sku"))
                    if sku:
                        names[sku] = name
                sku = _count(raw.get("sku"))
                if sku:
                    names[sku] = name
        return names


class OzonPerformanceClient(OzonJsonClient):
    """Рекламный кабинет Ozon: кампании и расход по SKU за день.

    Отчёт статистики Ozon готовит асинхронно: запрос возвращает UUID, готовность
    опрашивается, затем забирается сам отчёт. Отчётов — один за раз на кабинет и
    до 2000 в сутки, поэтому окно и состав кампаний режутся по потолкам Ozon.
    """

    api = OZON_PERFORMANCE_API
    api_name = "Ozon Performance API"

    async def campaigns(self, seller_id: str) -> list[OzonAdvertCampaign]:
        payload = await self.request("GET", "/api/client/campaign", seller_id)
        found: list[OzonAdvertCampaign] = []
        for raw in _listing(_mapping(payload).get("list")):
            raw = _mapping(raw)
            campaign_id = _count(raw.get("id")) or _int(raw.get("id"))
            if not campaign_id:
                continue
            found.append(
                OzonAdvertCampaign(
                    campaign_id=campaign_id,
                    title=str(raw.get("title") or ""),
                    state=str(raw.get("state") or ""),
                    object_type=str(raw.get("advObjectType") or ""),
                    payment_type=str(raw.get("PaymentType") or raw.get("paymentType") or ""),
                    created_at=_day(raw.get("createdAt")),
                    from_date=_day(raw.get("fromDate")),
                    to_date=_day(raw.get("toDate")),
                )
            )
        return found

    async def stats(
        self,
        seller_id: str,
        campaigns: Iterable[OzonAdvertCampaign],
        date_from: date,
        date_to: date,
        *,
        heartbeat: Callable[[], None] | None = None,
    ) -> list[OzonAdvertStat]:
        """Расход по SKU и дням за окно по кампаниям, которые это окно застали, кроме реферальных.

        Порцию, которую Ozon отверг целиком (HTTP 400), клиент перепрашивает по
        одной кампании: одна неподходящая кампания не должна оставлять без
        статистики девять остальных.
        """
        if (date_to - date_from).days >= STATS_MAX_WINDOW_DAYS:
            raise WBPermanentError(f"{self.api_name}: окно статистики не больше {STATS_MAX_WINDOW_DAYS} дней")
        wanted = sorted(
            {
                item.campaign_id
                for item in campaigns
                if item.object_type not in STATS_SKIPPED_OBJECT_TYPES and item.overlaps(date_from, date_to)
            }
        )
        stats: list[OzonAdvertStat] = []
        for offset in range(0, len(wanted), STATS_CAMPAIGNS_CHUNK):
            if heartbeat is not None:
                heartbeat()
            chunk = wanted[offset : offset + STATS_CAMPAIGNS_CHUNK]
            try:
                reports = [await self._report(seller_id, chunk, date_from, date_to, heartbeat)]
            except WBPermanentError as error:
                self.logger.warning("ozon_performance_chunk_refused: %s; перепрашиваю по одной", error)
                reports = []
                for campaign_id in chunk:
                    try:
                        reports.append(await self._report(seller_id, [campaign_id], date_from, date_to, heartbeat))
                    except WBPermanentError as single:
                        self.logger.warning("ozon_performance_campaign_refused: %s — %s", campaign_id, single)
            for report in reports:
                for key, raw in _mapping(report).items():
                    stats.extend(self._rows(_int(key), _mapping(raw)))
        return stats

    async def _report(
        self,
        seller_id: str,
        campaign_ids: list[int],
        date_from: date,
        date_to: date,
        heartbeat: Callable[[], None] | None,
    ) -> Any:
        created = await self.request(
            "POST",
            "/api/client/statistics/json",
            seller_id,
            json={
                "campaigns": [str(campaign_id) for campaign_id in campaign_ids],
                "dateFrom": date_from.isoformat(),
                "dateTo": date_to.isoformat(),
                "groupBy": "DATE",
            },
        )
        uuid_ = str(_mapping(created).get("UUID") or "")
        if not uuid_:
            raise WBPermanentError(f"{self.api_name}: отчёт статистики не создан — в ответе нет UUID")
        for _ in range(STATS_POLL_ATTEMPTS):
            status = _mapping(await self.request("GET", f"/api/client/statistics/{uuid_}", seller_id))
            state = str(status.get("state") or "")
            if state == "OK":
                return await self.request("GET", "/api/client/statistics/report", seller_id, params={"UUID": uuid_})
            if state == "ERROR":
                raise WBTemporaryError(f"{self.api_name}: отчёт {uuid_} не собрался: {status.get('error') or state}")
            if heartbeat is not None:
                heartbeat()
            await asyncio.sleep(STATS_POLL_SECONDS)
        raise WBTemporaryError(f"{self.api_name}: отчёт {uuid_} не готов за {STATS_POLL_ATTEMPTS} опросов")

    @staticmethod
    def _rows(campaign_id: int, raw: dict[str, Any]) -> list[OzonAdvertStat]:
        folded: dict[tuple[date, int], list[Any]] = {}
        for row in _listing(_mapping(raw.get("report")).get("rows")):
            row = _mapping(row)
            day = _day(row.get("date"))
            sku = _int(row.get("sku"))
            if day is None or not sku:
                continue
            values = folded.setdefault((day, sku), [0, 0, 0, ZERO, ZERO])
            values[0] += _int(row.get("views"))
            values[1] += _int(row.get("clicks"))
            values[2] += _int(row.get("orders"))
            values[3] += _report_money(row.get("moneySpent"))
            values[4] += _report_money(row.get("ordersMoney"))
        return [
            OzonAdvertStat(campaign_id, day, sku, values[0], values[1], values[2], values[3], values[4])
            for (day, sku), values in folded.items()
        ]


def _int(value: Any) -> int:
    """Числа в отчётах Performance приходят строками: «17041» и даже «17 041»."""
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int | float):
        return int(value)
    try:
        return int(str(value).replace("\xa0", "").replace(" ", "").split(",")[0] or 0)
    except ValueError:
        return 0


def _report_money(value: Any) -> Decimal:
    """Деньги в отчёте Performance — строка с запятой: «17041,06»."""
    if value is None or isinstance(value, bool):
        return ZERO
    text = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(text or 0).quantize(KOPECK)
    except (InvalidOperation, ValueError):
        return ZERO


def _day(value: Any) -> date | None:
    """Дата в отчёте — «19.10.2026»; на всякий случай принимается и ISO."""
    if not isinstance(value, str) or not value:
        return None
    for pattern in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[:10], pattern).date()
        except ValueError:
            continue
    return None
