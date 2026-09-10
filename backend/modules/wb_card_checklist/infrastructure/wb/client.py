import re
from datetime import UTC, datetime
from typing import Any

from backend.modules.wb_card_checklist.domain import CardFacts, PriceFacts, SubjectCharacteristic
from backend.modules.wb_core.infrastructure.wb import (
    EgressGateway,
    WBContentClient,
    WBJsonClient,
    WBPermanentError,
)

CONTENT_BUCKET = "content"
PRICES_BUCKET = "prices"
PRICES_PAGE_LIMIT = 1000
# Страховка от курсора, который WB вдруг перестал сдвигать: миллион товаров —
# больше, чем бывает в кабинете, а бесконечный цикл съел бы весь бюджет.
PRICES_MAX_PAGES = 1000
# Размеры фото в порядке предпочтения: миниатюра для таблицы, потом крупнее.
PHOTO_SIZES = ("c246x328", "square", "tm", "c516x688", "big")


class WBCharacteristicsClient(WBJsonClient):
    """Справочник характеристик предмета. Он общий для WB, но спрашивается ключом селлера."""

    bucket = CONTENT_BUCKET
    api_name = "WB Content API"
    category = "Контент"

    async def subject(self, seller_id: str, subject_id: int) -> list[SubjectCharacteristic]:
        payload = await self.request("GET", f"/content/v2/object/charcs/{subject_id}", seller_id) or {}
        if payload.get("error"):
            raise WBPermanentError(str(payload.get("errorText") or "WB отказал в справочнике характеристик"))
        data = payload.get("data") or []
        if not isinstance(data, list):
            raise WBPermanentError("WB вернул справочник характеристик не списком")
        return [parse_characteristic(item) for item in data if isinstance(item, dict) and _int(item.get("charcID"))]


class WBCardClient:
    """Карточки селлера со всем, что проверяет чек-лист, и справочник их предметов.

    Пагинация каталога общая с синком `wb_core`; здесь только разбор полей,
    которые каталогу не нужны: описание, видео, характеристики, дата создания.
    """

    def __init__(self, gateway: EgressGateway) -> None:
        self.catalog = WBContentClient(gateway)
        self.directory = WBCharacteristicsClient(gateway)

    async def cards(self, seller_id: str) -> list[CardFacts]:
        return [parse_card(raw) async for raw in self.catalog.raw_cards(seller_id)]

    async def characteristics(self, seller_id: str, subject_id: int) -> list[SubjectCharacteristic]:
        return await self.directory.subject(seller_id, subject_id)


class WBPricesClient(WBJsonClient):
    """Цены и скидки продавца. СПП в ответе нет: её WB официальным API не отдаёт."""

    bucket = PRICES_BUCKET
    api_name = "WB Prices API"
    category = "Цены и скидки"
    path = "/api/v2/list/goods/filter"

    async def prices(self, seller_id: str) -> list[PriceFacts]:
        collected: list[PriceFacts] = []
        offset = 0
        for _ in range(PRICES_MAX_PAGES):
            payload = (
                await self.request(
                    "GET", self.path, seller_id, params={"limit": str(PRICES_PAGE_LIMIT), "offset": str(offset)}
                )
                or {}
            )
            if payload.get("error"):
                raise WBPermanentError(str(payload.get("errorText") or "WB отказал в списке цен"))
            goods = (payload.get("data") or {}).get("listGoods") or []
            collected.extend(price for price in (parse_price(item) for item in goods) if price is not None)
            if len(goods) < PRICES_PAGE_LIMIT:
                return collected
            offset += len(goods)
        raise WBPermanentError(f"{self.api_name}: список цен не закончился за {PRICES_MAX_PAGES} страниц")


def parse_card(raw: dict[str, Any]) -> CardFacts:
    article = str(raw["nmID"])
    vendor_code = str(raw.get("vendorCode") or "")
    photos = raw.get("photos")
    return CardFacts(
        article=article,
        vendor_code=vendor_code,
        title=str(raw.get("title") or raw.get("subjectName") or vendor_code or article),
        barcode=_first_barcode(raw.get("sizes")),
        imt_id=_int(raw.get("imtID")),
        subject_id=_int(raw.get("subjectID")),
        subject_name=str(raw.get("subjectName") or ""),
        photo_url=_photo_url(photos),
        photo_count=len(photos) if isinstance(photos, list) else 0,
        description_length=len(str(raw.get("description") or "").strip()),
        # Поле есть только у карточек с видео; пустая строка — тоже «нет».
        has_video=bool(raw.get("video")),
        characteristic_ids=frozenset(_filled_characteristics(raw.get("characteristics"))),
        card_created_at=_moment(raw.get("createdAt")),
    )


def parse_characteristic(raw: dict[str, Any]) -> SubjectCharacteristic:
    return SubjectCharacteristic(
        charc_id=int(raw["charcID"]),
        name=str(raw.get("name") or ""),
        required=bool(raw.get("required")),
        popular=bool(raw.get("popular")),
        named_field=bool(raw.get("existNamedField")),
    )


def parse_price(raw: Any) -> PriceFacts | None:
    if not isinstance(raw, dict) or _int(raw.get("nmID")) is None:
        return None
    sizes = [size for size in raw.get("sizes") or [] if isinstance(size, dict)]
    prices = [float(size.get("price") or 0) for size in sizes]
    discounted = [float(size.get("discountedPrice") or 0) for size in sizes]
    return PriceFacts(
        article=str(raw["nmID"]),
        # Цены по размерам обычно одинаковы; если нет — на витрине покупатель
        # первым видит самую низкую цену со скидкой.
        price=max(prices, default=0.0),
        discounted_price=min(discounted, default=0.0),
        discount=int(raw.get("discount") or 0),
        club_discount=int(raw.get("clubDiscount") or 0),
    )


def _filled_characteristics(characteristics: Any) -> set[int]:
    """Ids of characteristics that actually carry a value."""
    if not isinstance(characteristics, list):
        return set()
    filled = set()
    for item in characteristics:
        if not isinstance(item, dict):
            continue
        charc_id = _int(item.get("id"))
        value = item.get("value")
        if charc_id is not None and value not in (None, "", []):
            filled.add(charc_id)
    return filled


def _first_barcode(sizes: Any) -> str:
    """The first barcode in the order WB lists sizes — what the seller's tables call «баркод»."""
    if not isinstance(sizes, list):
        return ""
    for size in sizes:
        if not isinstance(size, dict):
            continue
        for sku in size.get("skus") or []:
            barcode = str(sku).strip()
            if barcode:
                return barcode
    return ""


def _photo_url(photos: Any) -> str:
    if not isinstance(photos, list):
        return ""
    for photo in photos:
        if isinstance(photo, dict):
            for size in PHOTO_SIZES:
                value = photo.get(size)
                if isinstance(value, str) and value:
                    return value
    return ""


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    # WB бывает присылает наносекунды; fromisoformat понимает не больше микро.
    normalized = re.sub(r"(\.\d{6})\d+", r"\1", value.strip()).replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
