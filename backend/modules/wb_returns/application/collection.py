import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_returns.domain import Claim, ReturnChange
from backend.modules.wb_returns.infrastructure.postgres import ReturnsRepository
from backend.modules.wb_returns.infrastructure.wb import MAX_WINDOW_DAYS, WBClaimsClient, WBReturnsReportClient


@dataclass(frozen=True, slots=True)
class CollectionResult:
    returns_seen: int
    claims_seen: int
    changes: list[ReturnChange] = field(default_factory=list)
    new_claims: list[Claim] = field(default_factory=list)
    # Кабинет отключили, пока читали WB: писать нечего и некуда.
    skipped: bool = False


class CollectionService:
    """Отчёт о возвратах за окно и заявки покупателей — одним проходом по кабинету.

    Отчёт отдаётся целиком за окно, страниц у него нет; обычный проход берёт
    `window_days` назад, первый сбор кабинета — `backfill_days` (не больше 31,
    столько WB даёт за запрос). Открытые заявки читаются каждый проход, архив —
    раз в `claims_archive_hours`: он большой и меняется только решениями.
    """

    def __init__(
        self,
        session: AsyncSession,
        returns: ReturnsRepository,
        report_client: WBReturnsReportClient,
        claims_client: WBClaimsClient,
        *,
        window_days: int,
        backfill_days: int,
        claims_archive_hours: int,
    ) -> None:
        self.session = session
        self.returns = returns
        self.report_client = report_client
        self.claims_client = claims_client
        self.window_days = window_days
        self.backfill_days = min(backfill_days, MAX_WINDOW_DAYS)
        self.claims_archive_hours = claims_archive_hours

    async def collect(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> CollectionResult:
        stamp = now or datetime.now(UTC)
        today = stamp.date()
        seller_key = str(seller_id)
        tracked = await self.returns.tracked(seller_id)
        first_run = tracked is None or tracked.collected_at is None
        depth = self.backfill_days if first_run else min(self.window_days, MAX_WINDOW_DAYS)
        read_archive = (
            tracked is None
            or tracked.claims_archived_at is None
            or (tracked.claims_archived_at < stamp - timedelta(hours=self.claims_archive_hours))
        )
        # Сеть идёт до первой записи: держать транзакцию весь запрос незачем.
        await self.session.commit()

        items = await self.report_client.report(seller_key, today - timedelta(days=depth), today)
        if not await self.returns.still_tracked(seller_id):
            await self.session.rollback()
            return CollectionResult(len(items), 0, skipped=True)
        changes = await self.returns.upsert_returns(seller_id, items, now=stamp)
        # Отчёт — отдельная транзакция: упавший на заявках проход не должен
        # заставлять воркер перечитывать возвраты и терять их изменения.
        await self.session.commit()

        open_claims = await self.claims_client.claims(seller_key, archive=False)
        archived: list[Claim] = []
        if read_archive:
            archived = await self.claims_client.claims(seller_key, archive=True)
        if not await self.returns.still_tracked(seller_id):
            await self.session.rollback()
            return CollectionResult(len(items), len(open_claims) + len(archived), changes, skipped=True)
        new_claims = await self.returns.upsert_claims(seller_id, [*open_claims, *archived], now=stamp)
        await self.returns.close_missing_open_claims(seller_id, [claim.id for claim in open_claims], now=stamp)
        if read_archive:
            await self.returns.mark_claims_archived(seller_id, now=stamp)
        await self.returns.finish_collection(seller_id, now=stamp)
        await self.session.commit()
        return CollectionResult(len(items), len(open_claims) + len(archived), changes, new_claims)
