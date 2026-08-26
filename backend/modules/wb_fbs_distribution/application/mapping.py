import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_fbs_distribution.domain import BASIS_POINTS
from backend.modules.wb_fbs_distribution.infrastructure.postgres import FbsDistributionRepository


class InvalidShareError(Exception):
    """Правило деления пула, по которому нельзя считать."""


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Итог сопоставления одного кабинета."""

    matched: int
    catalog_sizes: int
    pools: int


@dataclass(frozen=True, slots=True)
class UnmappedPool:
    item_id: str
    characteristic: str
    barcode: str
    name: str
    on_hand: int


@dataclass(frozen=True, slots=True)
class SharedPool:
    """Пул, который расходуют несколько кабинетов."""

    item_id: str
    characteristic: str
    barcode: str
    name: str
    on_hand: int
    sellers: list[uuid.UUID]
    shares: dict[uuid.UUID, int]

    @property
    def rule_ready(self) -> bool:
        """Есть ли правило, по которому пул можно поделить.

        Доли должны покрывать ровно те кабинеты, что расходуют пул, и
        складываться в сто процентов. Иначе кабинеты вместе пообещают WB
        больше, чем есть, — или меньше.
        """
        if set(self.shares) != set(self.sellers):
            return False
        return sum(self.shares.values()) == BASIS_POINTS


@dataclass(frozen=True, slots=True)
class MappingState:
    pools: int
    mapped_pools: int
    unmapped: list[UnmappedPool]
    shared: list[SharedPool]

    @property
    def shared_without_rule(self) -> int:
        return sum(1 for pool in self.shared if not pool.rule_ready)


class MappingService:
    """Связь пулов 1С с размерами карточек WB по баркоду."""

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        distribution: FbsDistributionRepository,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.distribution = distribution

    async def rematch(self, seller_id: uuid.UUID) -> MatchResult:
        """Пересобрать связи кабинета по текущему каталогу и текущим пулам.

        Пересобрать целиком, а не дописать: и каталог, и снимок 1С меняются, и
        связь, потерявшая одну из сторон, обязана исчезнуть, а не указывать в
        пустоту.
        """
        catalog = await self.sellers.list_barcode_sizes(seller_id)
        pools = await self.distribution.pools(limit=1_000_000)
        rows = []
        for pool in pools:
            if not pool.barcode:
                continue
            found = catalog.get(pool.barcode)
            if found is None:
                continue
            chrt_id, article = found
            rows.append(
                {
                    "chrt_id": chrt_id,
                    "item_id": pool.item_id,
                    "characteristic": pool.characteristic,
                    "barcode": pool.barcode,
                    "article": article,
                }
            )
        await self.distribution.replace_mappings(seller_id, rows)
        await self.session.commit()
        return MatchResult(matched=len(rows), catalog_sizes=len(catalog), pools=len(pools))

    async def state(self, *, limit: int = 200) -> MappingState:
        totals = await self.distribution.pool_totals(reserve_units=0)
        mapped_keys = await self.distribution.mapped_pool_keys()
        unmapped = [
            UnmappedPool(
                item_id=pool.item_id,
                characteristic=pool.characteristic,
                barcode=pool.barcode,
                name=pool.name,
                on_hand=pool.quantity,
            )
            for pool in await self.distribution.unmapped_pools(limit=limit)
        ]

        counts = await self.distribution.pool_seller_counts()
        shared_keys = [key for key, count in counts.items() if count > 1]
        shares = await self.distribution.pool_shares()
        by_pool: dict[tuple[str, str], list[uuid.UUID]] = {}
        for mapping in await self.distribution.mappings():
            key = (mapping.item_id, mapping.characteristic)
            if key in counts and counts[key] > 1 and mapping.seller_id not in by_pool.setdefault(key, []):
                by_pool[key].append(mapping.seller_id)
        pools = {
            (pool.item_id, pool.characteristic): pool for pool in await self.distribution.pools_by_key(shared_keys)
        }
        shared = [
            SharedPool(
                item_id=key[0],
                characteristic=key[1],
                barcode=pools[key].barcode if key in pools else "",
                name=pools[key].name if key in pools else "",
                on_hand=pools[key].quantity if key in pools else 0,
                sellers=sorted(by_pool.get(key, []), key=str),
                shares=shares.get(key, {}),
            )
            for key in sorted(shared_keys)
        ]
        return MappingState(pools=totals[0], mapped_pools=len(mapped_keys), unmapped=unmapped, shared=shared)

    async def set_shares(self, item_id: str, characteristic: str, shares: dict[uuid.UUID, int]) -> MappingState:
        """Задать, как пул делится между кабинетами.

        Поровну по умолчанию не делим: одинаковый баркод в двух кабинетах ещё
        не значит, что запас между ними пополам. Это решение бизнеса, и без него
        пул в расчёт не идёт.
        """
        if any(share < 0 for share in shares.values()):
            raise InvalidShareError("Доля кабинета не может быть отрицательной")
        if shares and sum(shares.values()) != BASIS_POINTS:
            raise InvalidShareError("Доли кабинетов должны складываться ровно в 100%")
        await self.distribution.save_pool_shares(item_id, characteristic, shares)
        await self.session.commit()
        return await self.state()
