"""Снимок остатков на конец недели из зеркала wb_core.

Зеркало знает только «сейчас». Лист спрашивает «на последнюю неделю», поэтому
в начале недели остаток снимается и остаётся за закрытой неделей навсегда.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.fin_reports.domain import Stock
from backend.modules.fin_reports.infrastructure.postgres import FinReportsRepository
from backend.modules.wb_core.application import RemainsMirror
from backend.modules.wb_core.domain import WarehouseRemain
from backend.storage.pg import Database

# Служебные строки отчёта «Остатки на складах»: не склады, а путь товара и итог.
TO_CLIENT_ROW = "В пути до получателей"
FROM_CLIENT_ROW = "В пути возвраты на склад WB"
TOTAL_ROW = "Всего находится на складах"
SERVICE_ROWS = {TO_CLIENT_ROW, FROM_CLIENT_ROW, TOTAL_ROW}
# Снимок закрытой недели берётся не раньше этого часа понедельника: зеркало
# остатков обновляется раз в шесть часов, и ночной срез уже должен лечь.
SNAPSHOT_HOUR = 3


def last_closed_week_end(now: datetime, timezone: ZoneInfo) -> date | None:
    """Воскресенье недели, снимок которой уже пора снимать; `None` — ещё рано."""
    local = now.astimezone(timezone)
    monday = local.date() - timedelta(days=local.weekday())
    if local.weekday() == 0 and local.hour < SNAPSHOT_HOUR:
        return None
    return monday - timedelta(days=1)


def fold_remains(rows: list[WarehouseRemain]) -> dict[tuple[int, str], Stock]:
    """Строки отчёта по складам — в остаток по артикулу и размеру.

    «Всего» — склады плюс путь туда и обратно. Отчёт аналитики WB для этого не
    годится: у него «доступно к продаже» — 3 штуки при 249 на складах.
    """
    folded: dict[tuple[int, str], list[int]] = {}
    for row in rows:
        try:
            nm_id = int(row.article)
        except ValueError:
            continue
        values = folded.setdefault((nm_id, row.tech_size), [0, 0, 0])
        if row.warehouse_name == TO_CLIENT_ROW:
            values[1] += row.quantity
        elif row.warehouse_name == FROM_CLIENT_ROW:
            values[2] += row.quantity
        elif row.warehouse_name not in SERVICE_ROWS:
            values[0] += row.quantity
    return {key: Stock(values[0], values[1], values[2], sum(values)) for key, values in folded.items()}


@dataclass(frozen=True, slots=True)
class LiveStock:
    stocks: dict[tuple[int, str], Stock]
    taken_at: datetime | None


async def read_live_stock(session: AsyncSession, seller_id: uuid.UUID, *, now: datetime) -> LiveStock:
    """Остаток из зеркала прямо сейчас — для недели, снимок которой ещё не снят."""
    remains = await RemainsMirror(session).snapshot(seller_id)
    return LiveStock(fold_remains(list(remains.remains)), remains.collected_at)


class StockSnapshots:
    """Снимки закрытых недель — работа воркера."""

    def __init__(self, database: Database, *, timezone: ZoneInfo) -> None:
        self.database = database
        self.timezone = timezone

    async def take(self, seller_id: uuid.UUID, *, now: datetime) -> date | None:
        """Снимок последней закрытой недели, если его ещё нет. Возвращает её воскресенье."""
        week_end = last_closed_week_end(now, self.timezone)
        if week_end is None:
            return None
        async with self.database.session() as session:
            if week_end in await FinReportsRepository(session).snapshot_weeks(seller_id):
                return None
        async with self.database.session() as session:
            live = await read_live_stock(session, seller_id, now=now)
        if live.taken_at is None:
            # Зеркало до кабинета ещё не доходило: пустой снимок врал бы «остатка нет».
            return None
        async with self.database.session() as session:
            await FinReportsRepository(session).save_stock_snapshot(seller_id, week_end, live.stocks, now=now)
            await session.commit()
        return week_end
