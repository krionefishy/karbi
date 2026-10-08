from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from backend.modules.wb_core.domain import AdvertCampaign, AdvertNmStat, AdvertSpend
from backend.modules.wb_core.infrastructure.wb.client import WBPermanentError
from backend.modules.wb_core.infrastructure.wb.json_client import WBJsonClient

ADVERT_BUCKET = "advert"
# Потолки WB: кампаний в одном запросе и дней в окне.
IDS_CHUNK = 50
MAX_WINDOW_DAYS = 31
# Списания WB датирует московскими сутками; день берётся по Москве.
MOSCOW = ZoneInfo("Europe/Moscow")
KOPECK = Decimal("0.01")


def _money(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None:
        return Decimal("0.00")
    try:
        return Decimal(str(value)).quantize(KOPECK)
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _listing(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _count(value: Any) -> int:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class WBAdvertClient(WBJsonClient):
    """Рекламный API: списания по кампаниям, состав кампаний и статистика по артикулам.

    Лимиты WB разные у каждого метода (списания — раз в секунду, статистика —
    три в минуту), их держит шлюз; клиент только режет запросы по потолкам.
    """

    bucket = ADVERT_BUCKET
    api_name = "WB Advert API"
    category = "Продвижение"

    async def spend(self, seller_id: str, date_from: date, date_to: date) -> list[AdvertSpend]:
        """Списания по кампаниям за окно, сложенные по дню и типу оплаты."""
        if (date_to - date_from).days >= MAX_WINDOW_DAYS:
            raise WBPermanentError(f"{self.api_name}: окно списаний не больше {MAX_WINDOW_DAYS} дней")
        payload = await self.request(
            "GET", "/adv/v1/upd", seller_id, params={"from": date_from.isoformat(), "to": date_to.isoformat()}
        )
        folded: dict[tuple[int, date, str], Decimal] = defaultdict(Decimal)
        for raw in self._rows(payload, "списания"):
            moment = _moment(raw.get("updTime"))
            advert_id = raw.get("advertId")
            if moment is None or not isinstance(advert_id, int):
                continue
            key = (advert_id, moment.astimezone(MOSCOW).date(), str(raw.get("paymentType") or ""))
            folded[key] += _money(raw.get("updSum"))
        return [
            AdvertSpend(advert_id, day, payment_type, amount)
            for (advert_id, day, payment_type), amount in folded.items()
        ]

    async def campaigns(self, seller_id: str, advert_ids: Iterable[int]) -> list[AdvertCampaign]:
        """Состав и настройки кампаний по их номерам; неизвестные номера WB молча опускает."""
        wanted = sorted({advert_id for advert_id in advert_ids if advert_id})
        found: list[AdvertCampaign] = []
        for offset in range(0, len(wanted), IDS_CHUNK):
            chunk = wanted[offset : offset + IDS_CHUNK]
            payload = await self.request(
                "GET", "/api/advert/v2/adverts", seller_id, params={"ids": ",".join(map(str, chunk))}
            )
            adverts = payload.get("adverts") if isinstance(payload, dict) else payload
            for raw in self._rows(adverts, "кампании"):
                campaign = self._campaign(raw)
                if campaign is not None:
                    found.append(campaign)
        return found

    async def nm_stats(
        self, seller_id: str, advert_ids: Iterable[int], date_from: date, date_to: date
    ) -> list[AdvertNmStat]:
        """Статистика по артикулам и дням для кампаний, площадки сложены."""
        if (date_to - date_from).days >= MAX_WINDOW_DAYS:
            raise WBPermanentError(f"{self.api_name}: окно статистики не больше {MAX_WINDOW_DAYS} дней")
        wanted = sorted({advert_id for advert_id in advert_ids if advert_id})
        stats: list[AdvertNmStat] = []
        for offset in range(0, len(wanted), IDS_CHUNK):
            chunk = wanted[offset : offset + IDS_CHUNK]
            payload = await self.request(
                "GET",
                "/adv/v3/fullstats",
                seller_id,
                params={
                    "ids": ",".join(map(str, chunk)),
                    "beginDate": date_from.isoformat(),
                    "endDate": date_to.isoformat(),
                },
            )
            for raw in self._rows(payload, "статистика кампаний"):
                stats.extend(self._stats(raw))
        return stats

    def _rows(self, payload: Any, what: str) -> list[dict]:
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise WBPermanentError(f"{self.api_name}: {what} пришли не списком")
        return [row for row in payload if isinstance(row, dict)]

    @staticmethod
    def _campaign(raw: dict) -> AdvertCampaign | None:
        advert_id = raw.get("id")
        if not isinstance(advert_id, int):
            return None
        settings = _mapping(raw.get("settings"))
        timestamps = _mapping(raw.get("timestamps"))
        nm_settings = _listing(raw.get("nm_settings"))
        nm_ids = tuple(
            item["nm_id"] for item in nm_settings if isinstance(item, dict) and isinstance(item.get("nm_id"), int)
        )
        return AdvertCampaign(
            advert_id=advert_id,
            name=str(settings.get("name") or ""),
            status=_count(raw.get("status")),
            payment_type=str(settings.get("payment_type") or ""),
            bid_type=str(raw.get("bid_type") or ""),
            nm_ids=nm_ids,
            updated_at=_moment(timestamps.get("updated")),
        )

    @staticmethod
    def _stats(raw: dict) -> list[AdvertNmStat]:
        advert_id = raw.get("advertId")
        if not isinstance(advert_id, int):
            return []
        folded: dict[tuple[date, int], list[Any]] = {}
        for day in raw.get("days") or []:
            if not isinstance(day, dict):
                continue
            moment = _moment(day.get("date"))
            if moment is None:
                continue
            # Дата статистики — сутки без времени: берётся как есть, без сдвига по поясу.
            when = moment.date()
            for app in day.get("apps") or []:
                if not isinstance(app, dict):
                    continue
                for nm in app.get("nms") or []:
                    if not isinstance(nm, dict) or not isinstance(nm.get("nmId"), int):
                        continue
                    values = folded.setdefault((when, nm["nmId"]), [0, 0, 0, 0, 0, 0, Decimal("0.00"), Decimal("0.00")])
                    values[0] += _count(nm.get("views"))
                    values[1] += _count(nm.get("clicks"))
                    values[2] += _count(nm.get("orders"))
                    values[3] += _count(nm.get("shks"))
                    values[4] += _count(nm.get("atbs"))
                    values[5] += _count(nm.get("canceled"))
                    values[6] += _money(nm.get("sum"))
                    values[7] += _money(nm.get("sum_price"))
        return [
            AdvertNmStat(
                advert_id=advert_id,
                day=when,
                nm_id=nm_id,
                views=values[0],
                clicks=values[1],
                orders=values[2],
                shks=values[3],
                atbs=values[4],
                canceled=values[5],
                amount=values[6],
                orders_amount=values[7],
            )
            for (when, nm_id), values in folded.items()
        ]
