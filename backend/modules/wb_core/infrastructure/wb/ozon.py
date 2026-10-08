import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from backend.modules.wb_core.domain import (
    OZON_LINE_CONTAINER,
    OZON_LINE_DELIVERY,
    OZON_LINE_ITEM,
    OZON_LINE_NON_ITEM,
    OZON_LINE_SALE,
    OzonAccrualLine,
)
from backend.modules.wb_core.infrastructure.wb.client import WBPermanentError
from backend.modules.wb_core.infrastructure.wb.egress import EgressGateway

OZON_SELLER_API = "seller"
OZON_PERFORMANCE_API = "performance"
# Страниц начислений за день — предохранитель от курсора, который не двигается.
MAX_PAGES = 200
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

    async def request(self, method: str, path: str, seller_id: str, *, json: Any | None = None) -> Any:
        return await self.gateway.call_ozon(
            seller_id=seller_id,
            api=self.api,
            method=method,
            path=path,
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
