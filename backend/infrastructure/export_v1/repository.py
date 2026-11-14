from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import String, and_, cast, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from backend.modules.wb_card_checklist.infrastructure.postgres.models import CardFactsModel, PriceFactsModel
from backend.modules.wb_core.infrastructure.postgres.models import (
    AdvertCampaignModel,
    AdvertNmStatModel,
    ArticleModel,
    FbsWarehouseStockModel,
    ReviewFactModel,
    SalesReportRowModel,
    SellerModel,
    StockFactModel,
    WarehouseRemainModel,
)
from backend.modules.wb_turnover.infrastructure.postgres.models import OrderModel

CONTRACT = "export_v1"
SCHEMA_VERSION = 1
DATASETS = (
    "sellers",
    "products",
    "orders",
    "sales_rows",
    "prices",
    "ad_campaigns",
    "ad_stats_daily",
    "stocks",
    "ratings",
)
MOSCOW = ZoneInfo("Europe/Moscow")


class BadCursor(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Cursor:
    updated_at: datetime
    pk: str


@dataclass(frozen=True, slots=True)
class ExportRow:
    updated_at: datetime
    pk: str
    item: dict[str, Any]


def utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def iso(value: datetime) -> str:
    return utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def encode_cursor(dataset: str, cursor: Cursor, secret: str) -> str:
    payload = json.dumps(
        {"v": SCHEMA_VERSION, "d": dataset, "u": iso(cursor.updated_at), "p": cursor.pk},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    signature = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def decode_cursor(dataset: str, value: str, secret: str) -> Cursor:
    try:
        if not value or len(value) > 4096:
            raise ValueError
        encoded, encoded_signature = value.split(".", 1)
        payload = _unb64(encoded)
        signature = _unb64(encoded_signature)
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        raw = json.loads(payload)
        if raw != {"d": dataset, "p": raw.get("p"), "u": raw.get("u"), "v": SCHEMA_VERSION}:
            raise ValueError
        updated_at = datetime.fromisoformat(str(raw["u"]).replace("Z", "+00:00"))
        if updated_at.tzinfo is None or not isinstance(raw["p"], str) or not raw["p"]:
            raise ValueError
        return Cursor(utc(updated_at), raw["p"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise BadCursor("cursor is not valid for this dataset") from error


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _pk(*values: Any):
    """A unique, deterministic textual ordering for a source primary key."""
    return cast(func.jsonb_build_array(*values), String)


def _money(value: Decimal | int | float | None) -> int:
    amount = Decimal(str(value or 0)) * 100
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _basis_points(value: Decimal | int | float | None) -> int:
    points = Decimal(str(value or 0)) * 100
    return int(points.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _nm(value: str | int) -> int:
    return int(value)


def _barcodes(article: ArticleModel, card: CardFactsModel | None = None) -> list[str]:
    collected: list[str] = []
    for size in article.sizes or []:
        if not isinstance(size, dict):
            continue
        for raw in size.get("skus") or []:
            barcode = str(raw).strip()
            if barcode and barcode not in collected:
                collected.append(barcode)
    if card is not None and card.barcode and card.barcode not in collected:
        collected.append(card.barcode)
    return collected


def _first_barcode(article: ArticleModel | None) -> str:
    barcodes = _barcodes(article) if article else []
    return barcodes[0] if barcodes else ""


def _moscow_date(value: date | None, *timestamps: datetime | None) -> str:
    if value is not None:
        return value.isoformat()
    fallback = next(timestamp for timestamp in timestamps if timestamp is not None)
    return utc(fallback).astimezone(MOSCOW).date().isoformat()


Mapper = Callable[[Any], dict[str, Any]]


class ExportRepository:
    """Only SELECTs over the source schemas; this adapter has no write methods."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def page(self, dataset: str, cursor: Cursor | None, limit: int) -> tuple[list[ExportRow], bool]:
        if dataset == "stocks":
            return await self._stocks(cursor, limit)
        statement, updated_at, pk, mapper = self._query(dataset)
        if dataset == "sales_rows":
            rows = await self._execute_sales_rows(statement, updated_at, pk, mapper, cursor, limit + 1)
            return rows[:limit], len(rows) > limit
        rows = await self._execute(statement, updated_at, pk, mapper, cursor, limit + 1)
        return rows[:limit], len(rows) > limit

    async def volume_counts(self) -> dict[str, int]:
        """Count the rows the exporter would expose, without serializing payloads."""
        counts: dict[str, int] = {}
        for dataset in DATASETS:
            if dataset == "stocks":
                count = 0
                for statement, _updated, _pk_value, _mapper in self._stock_queries():
                    count += int(
                        await self.session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
                        or 0
                    )
                counts[dataset] = count
                continue
            statement, _updated, _pk_value, _mapper = self._query(dataset)
            counts[dataset] = int(
                await self.session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
            )
        return counts

    async def _execute(
        self,
        statement: Select,
        updated_at: Any,
        pk: Any,
        mapper: Mapper,
        cursor: Cursor | None,
        limit: int,
    ) -> list[ExportRow]:
        export_updated = updated_at.label("_export_updated")
        export_pk = pk.label("_export_pk")
        query = statement.add_columns(export_updated, export_pk)
        if cursor is not None:
            query = query.where(
                or_(
                    updated_at > cursor.updated_at,
                    and_(updated_at == cursor.updated_at, pk > cursor.pk),
                )
            )
        result = await self.session.execute(query.order_by(updated_at, pk).limit(limit))
        output: list[ExportRow] = []
        for row in result.all():
            stamp = utc(row._export_updated)
            item = mapper(row)
            item.update(seller_id=str(item["seller_id"]), channel="wb", updated_at=iso(stamp))
            output.append(ExportRow(stamp, str(row._export_pk), item))
        return output

    async def _execute_sales_rows(
        self,
        statement: Select,
        updated_at: Any,
        pk: Any,
        mapper: Mapper,
        cursor: Cursor | None,
        limit: int,
    ) -> list[ExportRow]:
        """Use the deployed ``(collected_at, seller_id, rrd_id)`` index.

        The public cursor keeps its existing JSON primary-key representation,
        but comparing that representation as text defeats the index and makes
        every page re-sort a large collection batch. Decode it only for the
        keyset predicate and preserve the wire format returned to consumers.
        """

        seller_id = SalesReportRowModel.seller_id
        rrd_id = SalesReportRowModel.rrd_id
        export_updated = updated_at.label("_export_updated")
        export_pk = pk.label("_export_pk")
        query = statement.add_columns(export_updated, export_pk)
        if cursor is not None:
            try:
                raw_key = json.loads(cursor.pk)
                if not isinstance(raw_key, list) or len(raw_key) != 2 or isinstance(raw_key[1], bool):
                    raise ValueError
                cursor_seller = uuid.UUID(str(raw_key[0]))
                cursor_rrd = int(raw_key[1])
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise BadCursor("cursor is not valid for sales_rows") from error
            query = query.where(
                or_(
                    updated_at > cursor.updated_at,
                    and_(
                        updated_at == cursor.updated_at,
                        or_(seller_id > cursor_seller, and_(seller_id == cursor_seller, rrd_id > cursor_rrd)),
                    ),
                )
            )
        result = await self.session.execute(query.order_by(updated_at, seller_id, rrd_id).limit(limit))
        output: list[ExportRow] = []
        for row in result.all():
            stamp = utc(row._export_updated)
            item = mapper(row)
            item.update(seller_id=str(item["seller_id"]), channel="wb", updated_at=iso(stamp))
            output.append(ExportRow(stamp, str(row._export_pk), item))
        return output

    def _query(self, dataset: str) -> tuple[Select, Any, Any, Mapper]:
        return getattr(self, f"_{dataset}_query")()

    def _sellers_query(self):
        updated = SellerModel.updated_at
        pk = _pk(SellerModel.id)

        def mapper(row):
            seller = row[0]
            return {
                "seller_id": seller.id,
                "name": seller.name,
                "archived": seller.archived_at is not None,
                "wb_status": seller.egress_status,
                "ozon_status": seller.ozon_egress_status,
            }

        return select(SellerModel), updated, pk, mapper

    def _products_query(self):
        updated = func.greatest(
            ArticleModel.updated_at,
            func.coalesce(CardFactsModel.collected_at, ArticleModel.updated_at),
        )
        pk = _pk(ArticleModel.id)
        statement = select(ArticleModel, CardFactsModel).outerjoin(
            CardFactsModel,
            and_(
                CardFactsModel.seller_id == ArticleModel.seller_id,
                CardFactsModel.article == ArticleModel.article,
            ),
        )

        def mapper(row):
            article, card = row[0], row[1]
            return {
                "seller_id": article.seller_id,
                "nm_id": _nm(article.article),
                "vendor_code": article.vendor_code,
                "title": card.title if card and card.title else article.name,
                "brand": article.brand,
                "subject_name": article.subject_name,
                "barcodes": _barcodes(article, card),
                "photo_url": article.photo_url,
                "state": article.state,
            }

        return statement, updated, pk, mapper

    def _orders_query(self):
        updated = OrderModel.collected_at
        pk = _pk(OrderModel.srid)
        statement = select(OrderModel, ArticleModel).outerjoin(
            ArticleModel,
            and_(ArticleModel.seller_id == OrderModel.seller_id, ArticleModel.article == OrderModel.article),
        )

        def mapper(row):
            order, article = row[0], row[1]
            return {
                "seller_id": order.seller_id,
                "srid": order.srid,
                "nm_id": _nm(order.article),
                "vendor_code": article.vendor_code if article else "",
                "barcode": order.barcode,
                "order_date": order.order_date.isoformat(),
                "last_change_at": iso(order.last_change_date),
                "is_cancelled": order.is_cancel,
                "price_kop": _money(order.price),
                "warehouse_type": order.warehouse_type,
            }

        return statement, updated, pk, mapper

    def _sales_rows_query(self):
        updated = SalesReportRowModel.collected_at
        pk = _pk(SalesReportRowModel.seller_id, SalesReportRowModel.rrd_id)

        def mapper(row):
            item = row[0]
            commission = item.commission_percent if item.commission_percent else item.kvw
            return {
                "seller_id": item.seller_id,
                "rrd_id": item.rrd_id,
                "report_id": item.report_id,
                "doc_type": item.doc_type_name,
                "oper_name": item.seller_oper_name,
                "srid": item.srid,
                "nm_id": item.nm_id,
                "vendor_code": item.vendor_code,
                "barcode": item.sku,
                "order_dt": iso(item.order_dt) if item.order_dt else None,
                "sale_dt": iso(item.sale_dt) if item.sale_dt else None,
                "rr_date": _moscow_date(item.rr_date, item.sale_dt, item.order_dt, item.collected_at),
                "quantity": item.quantity,
                "retail_price_withdisc_kop": _money(item.retail_price_withdisc),
                "retail_amount_kop": _money(item.retail_amount),
                "spp_bp": _basis_points(item.spp),
                "commission_bp": _basis_points(commission),
                "for_pay_kop": _money(item.for_pay),
                "delivery_service_kop": _money(item.delivery_service),
                "paid_storage_kop": _money(item.paid_storage),
                "paid_acceptance_kop": _money(item.paid_acceptance),
                "penalty_kop": _money(item.penalty),
                "deduction_kop": _money(item.deduction),
                "additional_payment_kop": _money(item.additional_payment),
                "acquiring_fee_kop": _money(item.acquiring_fee),
                "ppvz_reward_kop": _money(item.ppvz_reward),
                "delivery_amount": item.delivery_amount,
                "return_amount": item.return_amount,
            }

        return select(SalesReportRowModel), updated, pk, mapper

    def _prices_query(self):
        updated = PriceFactsModel.collected_at
        pk = _pk(PriceFactsModel.seller_id, PriceFactsModel.article)

        def mapper(row):
            item = row[0]
            return {
                "seller_id": item.seller_id,
                "nm_id": _nm(item.article),
                "as_of": iso(item.collected_at),
                "price_kop": _money(item.price),
                "discounted_price_kop": _money(item.discounted_price),
                "discount_pct": item.discount,
                "club_discount_pct": item.club_discount,
                "club_price_kop": _money(item.club_discounted_price)
                if item.club_discounted_price is not None
                else None,
            }

        return select(PriceFactsModel), updated, pk, mapper

    def _ad_campaigns_query(self):
        # collected_at is the local commit timestamp and therefore the safe cursor timestamp.
        updated = AdvertCampaignModel.collected_at
        pk = _pk(AdvertCampaignModel.seller_id, AdvertCampaignModel.advert_id)

        def mapper(row):
            item = row[0]
            return {
                "seller_id": item.seller_id,
                "advert_id": item.advert_id,
                "name": item.name,
                "status": item.status,
                "payment_type": item.payment_type,
                "bid_type": item.bid_type,
                "nm_ids": [int(value) for value in item.nm_ids or []],
            }

        return select(AdvertCampaignModel), updated, pk, mapper

    def _ad_stats_daily_query(self):
        updated = AdvertNmStatModel.collected_at
        pk = _pk(
            AdvertNmStatModel.seller_id,
            AdvertNmStatModel.advert_id,
            AdvertNmStatModel.day,
            AdvertNmStatModel.nm_id,
            literal("all"),
        )

        def mapper(row):
            item = row[0]
            return {
                "seller_id": item.seller_id,
                "advert_id": item.advert_id,
                "day": item.day.isoformat(),
                "nm_id": item.nm_id,
                "placement": "all",
                "views": item.views,
                "clicks": item.clicks,
                "orders": item.orders,
                "atbs": item.atbs,
                "spend_kop": _money(item.amount),
                "orders_sum_kop": _money(item.orders_amount),
            }

        return select(AdvertNmStatModel), updated, pk, mapper

    def _ratings_query(self):
        updated = ReviewFactModel.collected_at
        pk = _pk(ReviewFactModel.seller_id, ReviewFactModel.article)

        def mapper(row):
            item = row[0]
            rating = (
                int((Decimal(str(item.wb_rating)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                if item.wb_rating is not None
                else None
            )
            return {
                "seller_id": item.seller_id,
                "nm_id": _nm(item.article),
                "as_of": iso(item.collected_at),
                "rating_x100": rating,
                "count_1": item.count_rating_1,
                "count_2": item.count_rating_2,
                "count_3": item.count_rating_3,
                "count_4": item.count_rating_4,
                "count_5": item.count_rating_5,
                "with_photo": item.count_with_photo,
                "with_video": item.count_with_video,
            }

        return select(ReviewFactModel), updated, pk, mapper

    async def _stocks(self, cursor: Cursor | None, limit: int) -> tuple[list[ExportRow], bool]:
        branches = self._stock_queries()
        pages = [await self._execute(*branch, cursor, limit + 1) for branch in branches]
        merged = sorted((item for page in pages for item in page), key=lambda item: (item.updated_at, item.pk))
        return merged[:limit], len(merged) > limit

    def _stock_queries(self) -> list[tuple[Select, Any, Any, Mapper]]:
        remains_updated = WarehouseRemainModel.collected_at
        remains_pk = _pk(
            WarehouseRemainModel.seller_id,
            literal("fbo"),
            literal(None, type_=String()),
            WarehouseRemainModel.warehouse_name,
            WarehouseRemainModel.article,
            WarehouseRemainModel.barcode,
        )

        def remains_mapper(row):
            item = row[0]
            return {
                "seller_id": item.seller_id,
                "kind": "fbo",
                "warehouse_id": None,
                "warehouse_name": item.warehouse_name,
                "nm_id": _nm(item.article),
                "barcode": item.barcode,
                "as_of": iso(item.collected_at),
                "quantity": item.quantity,
                "in_way_to_client": 0,
                "in_way_from_client": 0,
            }

        fbs_updated = FbsWarehouseStockModel.collected_at
        fbs_pk = _pk(
            FbsWarehouseStockModel.seller_id,
            literal("fbs"),
            FbsWarehouseStockModel.warehouse_id,
            literal(""),
            FbsWarehouseStockModel.article,
            literal(""),
        )
        fbs_statement = select(FbsWarehouseStockModel, ArticleModel).outerjoin(
            ArticleModel,
            and_(
                ArticleModel.seller_id == FbsWarehouseStockModel.seller_id,
                ArticleModel.article == FbsWarehouseStockModel.article,
            ),
        )

        def fbs_mapper(row):
            item, article = row[0], row[1]
            return {
                "seller_id": item.seller_id,
                "kind": "fbs",
                "warehouse_id": item.warehouse_id,
                "warehouse_name": "",
                "nm_id": _nm(item.article),
                "barcode": _first_barcode(article),
                "as_of": iso(item.collected_at),
                "quantity": item.quantity,
                "in_way_to_client": 0,
                "in_way_from_client": 0,
            }

        output: list[tuple[Select, Any, Any, Mapper]] = [
            (select(WarehouseRemainModel), remains_updated, remains_pk, remains_mapper),
            (fbs_statement, fbs_updated, fbs_pk, fbs_mapper),
        ]
        for kind, quantity, detailed in (
            ("fbo", StockFactModel.fbo_quantity, WarehouseRemainModel),
            ("fbs", StockFactModel.fbs_quantity, FbsWarehouseStockModel),
        ):
            updated = StockFactModel.collected_at
            pk = _pk(
                StockFactModel.seller_id,
                literal(kind),
                literal(None, type_=String()),
                literal("all"),
                StockFactModel.article,
                literal(""),
            )
            detailed_exists = exists().where(
                detailed.seller_id == StockFactModel.seller_id,
                detailed.article == StockFactModel.article,
            )
            statement = (
                select(StockFactModel, ArticleModel, quantity.label("_quantity"))
                .outerjoin(
                    ArticleModel,
                    and_(
                        ArticleModel.seller_id == StockFactModel.seller_id,
                        ArticleModel.article == StockFactModel.article,
                    ),
                )
                .where(~detailed_exists)
            )

            def aggregate_mapper(row, stock_kind=kind):
                item, article = row[0], row[1]
                return {
                    "seller_id": item.seller_id,
                    "kind": stock_kind,
                    "warehouse_id": None,
                    "warehouse_name": "all",
                    "nm_id": _nm(item.article),
                    "barcode": _first_barcode(article),
                    "as_of": iso(item.collected_at),
                    "quantity": row._quantity,
                    "in_way_to_client": 0,
                    "in_way_from_client": 0,
                }

            output.append((statement, updated, pk, aggregate_mapper))
        return output


def cursor_from_row(row: ExportRow) -> Cursor:
    return Cursor(row.updated_at, row.pk)


def day(value: date | None) -> str | None:
    return value.isoformat() if value else None
