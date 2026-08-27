import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_fbs_distribution.domain import StockSnapshot, available_units
from backend.modules.wb_fbs_distribution.infrastructure.postgres import FbsDistributionRepository

# Идентификатор источника, которым подпишется будущий обмен с 1С.
ONEC = "1c"
DISCONNECTED = "disconnected"

ACCEPTED = "accepted"
REJECTED = "rejected"

# Часы вперёд, которые ещё можно списать на разъехавшиеся часы, а не на
# испорченный файл.
CLOCK_SKEW = timedelta(minutes=5)


class SnapshotRejected(Exception):
    """Снимок принят быть не может: по нему нельзя считать распределение."""


class StockSnapshotSource(ABC):
    """Откуда берётся абсолютный остаток 1С.

    Порт, а не клиент: транспорт с 1С ещё не согласован, и всё, что выше —
    проверка, пулы, расчёт — не должно про него знать. Когда появится реальный
    обмен, рядом встанет второй адаптер, и меняться будет только регистрация.
    """

    source_id: str

    @abstractmethod
    async def fetch(self) -> StockSnapshot | None:
        """Снимок или None, если источник ничего нового не отдал."""


class DisconnectedSource(StockSnapshotSource):
    """Заглушка на время, пока обмена с 1С нет.

    Всегда отвечает «нечего брать». Автоматизация из-за этого не считает
    распределение — и это верное поведение: без остатка 1С считать нечего, а
    выдумать его нельзя. Оператор пока грузит снимок файлом.
    """

    source_id = DISCONNECTED

    async def fetch(self) -> StockSnapshot | None:
        return None


@dataclass(frozen=True, slots=True)
class PoolRow:
    item_id: str
    characteristic: str
    barcode: str
    name: str
    on_hand: int
    available: int


@dataclass(frozen=True, slots=True)
class SnapshotState:
    """Что сейчас известно про остаток 1С."""

    snapshot_id: uuid.UUID | None
    source: str
    generated_at: datetime | None
    received_at: datetime | None
    lines: int
    stale: bool
    pools: int
    on_hand_total: int
    available_total: int


class SnapshotService:
    """Приём абсолютного снимка 1С и пулы, которые из него получаются."""

    def __init__(
        self,
        session: AsyncSession,
        distribution: FbsDistributionRepository,
        *,
        max_age_minutes: int,
    ) -> None:
        self.session = session
        self.distribution = distribution
        self.max_age = timedelta(minutes=max_age_minutes)

    async def accept(self, snapshot: StockSnapshot, *, source: str, now: datetime | None = None) -> SnapshotState:
        """Принять снимок целиком или не принять вовсе.

        Частично принятый снимок хуже отвергнутого: расчёт пошёл бы по смеси
        свежих и вчерашних строк, и объяснить опубликованное число стало бы
        нечем.
        """
        stamp = now or datetime.now(UTC)
        try:
            self._validate(snapshot, stamp)
        except SnapshotRejected as error:
            await self.distribution.record_snapshot(
                source=source,
                generated_at=snapshot.generated_at,
                received_at=stamp,
                lines=len(snapshot.lines),
                status=REJECTED,
                error=str(error),
            )
            await self.session.commit()
            raise

        snapshot_id = await self.distribution.record_snapshot(
            source=source,
            generated_at=snapshot.generated_at,
            received_at=stamp,
            lines=len(snapshot.lines),
            status=ACCEPTED,
            error=None,
        )
        await self.distribution.replace_pools(snapshot.lines, snapshot_id=snapshot_id, now=stamp)
        await self.session.commit()
        return await self.state(now=stamp)

    def _validate(self, snapshot: StockSnapshot, now: datetime) -> None:
        if not snapshot.lines:
            raise SnapshotRejected("В снимке нет ни одной строки")
        if snapshot.generated_at > now + CLOCK_SKEW:
            raise SnapshotRejected("Время формирования снимка в будущем")
        negative = [line.barcode or line.item_id for line in snapshot.lines if line.quantity < 0]
        if negative:
            raise SnapshotRejected(f"Отрицательный остаток у {len(negative)} строк, первая: {negative[0]}")
        keys: set[tuple[str, str]] = set()
        barcodes: set[str] = set()
        for line in snapshot.lines:
            key = (line.item_id, line.characteristic)
            if key in keys:
                raise SnapshotRejected(f"Номенклатура {line.item_id} встречается дважды с одной характеристикой")
            keys.add(key)
            if not line.barcode:
                continue
            if line.barcode in barcodes:
                # Один баркод — один размер карточки WB. Две строки на один
                # баркод означают, что остаток задвоится при сопоставлении.
                raise SnapshotRejected(f"Баркод {line.barcode} встречается дважды")
            barcodes.add(line.barcode)

    async def state(self, *, now: datetime | None = None) -> SnapshotState:
        stamp = now or datetime.now(UTC)
        header = await self.distribution.latest_snapshot()
        totals = await self.distribution.pool_totals(reserve_units=(await self.distribution.settings()).reserve_units)
        if header is None:
            return SnapshotState(
                snapshot_id=None,
                source=DISCONNECTED,
                generated_at=None,
                received_at=None,
                lines=0,
                stale=True,
                pools=totals[0],
                on_hand_total=totals[1],
                available_total=totals[2],
            )
        return SnapshotState(
            snapshot_id=header.id,
            source=header.source,
            generated_at=header.generated_at,
            received_at=header.received_at,
            lines=header.lines,
            stale=stamp - header.generated_at > self.max_age,
            pools=totals[0],
            on_hand_total=totals[1],
            available_total=totals[2],
        )

    async def pools(self, *, limit: int = 200, search: str = "") -> list[PoolRow]:
        reserve = (await self.distribution.settings()).reserve_units
        rows = await self.distribution.pools(limit=limit, search=search)
        return [
            PoolRow(
                item_id=row.item_id,
                characteristic=row.characteristic,
                barcode=row.barcode,
                name=row.name,
                on_hand=row.quantity,
                available=available_units(row.quantity, reserve),
            )
            for row in rows
        ]
