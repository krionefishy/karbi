"""Реклама по артикулам за неделю из зеркала рекламы.

Списание WB идёт по кампании с типом оплаты, а кампания продвигает один или
несколько артикулов. Списание кампании делится между её артикулами по доле
статистики (`sum` из fullstats за те же дни); без статистики — поровну между
артикулами кампании; кампания, которой зеркало не знает, уходит в строку без
артикула. Справочная «реклама из Продвижения» — статистика по артикулу как есть.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.fin_reports.domain import AdSpend
from backend.modules.wb_core.application import AdvertMirror

ZERO = Decimal("0")
KOPECK = Decimal("0.01")
# Типы оплаты, как их называет WB в списаниях.
BALANCE = "баланс"
ACCOUNT = "счет"
BONUS = "бонус"


@dataclass(slots=True)
class _Sums:
    balance: Decimal = ZERO
    account: Decimal = ZERO
    bonus: Decimal = ZERO
    info: Decimal = ZERO

    def add(self, payment_type: str, amount: Decimal) -> None:
        name = payment_type.lower().replace("ё", "е")
        if name.startswith(ACCOUNT):
            self.account += amount
        elif name.startswith(BONUS):
            self.bonus += amount
        else:
            self.balance += amount

    def spend(self) -> AdSpend:
        return AdSpend(
            self.balance.quantize(KOPECK),
            self.account.quantize(KOPECK),
            self.bonus.quantize(KOPECK),
            self.info.quantize(KOPECK),
        )


@dataclass(frozen=True, slots=True)
class WeekAds:
    """Реклама недели по артикулам и остаток, который ни к какому артикулу не привязать."""

    by_article: dict[int, AdSpend] = field(default_factory=dict)
    unallocated: AdSpend = field(default_factory=AdSpend)
    # Последний день, который зеркало рекламы прочитало целиком; `None` — не читало.
    collected_through: date | None = None

    def of(self, nm_id: int) -> AdSpend:
        return self.by_article.get(nm_id, AdSpend())


async def week_ads(session: AsyncSession, seller_id: uuid.UUID, *, since: date, until: date) -> WeekAds:
    mirror = AdvertMirror(session)
    spend = await mirror.spend(seller_id, since=since, until=until)
    stats = await mirror.nm_stats(seller_id, since=since, until=until)
    campaigns = await mirror.campaigns(seller_id, {item.advert_id for item in spend})
    # Доля артикула в кампании — по статистике за неделю.
    weights: dict[int, dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    sums: dict[int, _Sums] = defaultdict(_Sums)
    for stat in stats:
        weights[stat.advert_id][stat.nm_id] += stat.amount
        sums[stat.nm_id].info += stat.amount
    unallocated = _Sums()
    for item in spend:
        shares = _shares(weights.get(item.advert_id, {}), campaigns.get(item.advert_id))
        if not shares:
            unallocated.add(item.payment_type, item.amount)
            continue
        for nm_id, share in shares.items():
            sums[nm_id].add(item.payment_type, item.amount * share)
    return WeekAds(
        by_article={nm_id: value.spend() for nm_id, value in sums.items()},
        unallocated=unallocated.spend(),
        collected_through=await mirror.collected_through(seller_id),
    )


def _shares(weights: dict[int, Decimal], campaign) -> dict[int, Decimal]:
    """Доли артикулов в списании кампании: по статистике, без неё — поровну по составу."""
    total = sum(weights.values(), ZERO)
    if total > 0:
        return {nm_id: weight / total for nm_id, weight in weights.items() if weight > 0}
    if campaign is not None and campaign.nm_ids:
        share = Decimal(1) / len(campaign.nm_ids)
        return dict.fromkeys(campaign.nm_ids, share)
    return {}
