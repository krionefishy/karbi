"""KPI по скриншотам: плашка менеджера, сводка по менеджерам и выгрузка по дням.

Засчитывается только `accepted`; решения админа уже отражены в вердикте, отдельного учёта нет.
Границы — даты по Москве по `received_at`.
"""

import uuid
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_screenshot_control.application.periods import Period, preliminary
from backend.modules.wb_screenshot_control.infrastructure.postgres.queries import Counts, ScreenshotQueries, Window
from backend.shared.settings.config import ScreenshotControlConfig

# Флаги — сигнал для разговора, а не санкция: на малом числе скринов доли ничего не значат.
FLAG_MIN_TOTAL = 20
DUPLICATE_RATE_FLAG = 0.10
REVIEW_RATE_FLAG = 0.30
REJECT_RATE_FLAG = 0.30


def window_of(period: Period, timezone: str) -> Window:
    start, end = period.bounds(ZoneInfo(timezone))
    return Window(start, end)


def _rate(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


class KpiService:
    def __init__(self, session: AsyncSession, config: ScreenshotControlConfig) -> None:
        self.queries = ScreenshotQueries(session)
        self.config = config

    def preliminary(self, period: Period) -> bool:
        return preliminary(self.config.mode, period, self.config.kpi_from)

    async def summary(self, user_id: uuid.UUID, period: Period) -> dict[str, Any]:
        counts = await self.queries.counts(window_of(period, self.config.timezone), user_id)
        return {
            "period": {"from": period.start, "to": period.end},
            "mode": self.config.mode,
            "preliminary": self.preliminary(period),
            "counts": {
                "positive": counts.positive,
                "amended": counts.amended,
                "deleted": counts.deleted,
                "accepted_total": counts.accepted,
            },
            "pending_review": counts.needs_review,
            "awaiting_outcome": counts.awaiting_outcome,
            "processing": counts.processing,
            "duplicates": counts.duplicate,
            "rejected": counts.rejected,
        }

    async def managers(self, period: Period, usernames: dict[uuid.UUID, str]) -> dict[str, Any]:
        window = window_of(period, self.config.timezone)
        bursts = await self.queries.users_with_reason(window, "upload_burst")
        items: list[dict[str, Any]] = []
        for row in await self.queries.counts_by_user(window):
            counts = row.counts
            rates = {
                "duplicate_rate": _rate(counts.duplicate, counts.total),
                "review_rate": _rate(counts.needs_review, counts.total),
                "reject_rate": _rate(counts.rejected, counts.total),
            }
            flags = []
            if counts.total >= FLAG_MIN_TOTAL:
                if rates["duplicate_rate"] > DUPLICATE_RATE_FLAG:
                    flags.append("high_duplicate_rate")
                if rates["review_rate"] > REVIEW_RATE_FLAG:
                    flags.append("high_review_rate")
                if rates["reject_rate"] > REJECT_RATE_FLAG:
                    flags.append("high_reject_rate")
            if row.user_id in bursts:
                flags.append("upload_burst")
            items.append(
                {
                    "user": {"id": row.user_id, "username": usernames.get(row.user_id, "")},
                    **_stats(counts),
                    **rates,
                    "last_submission_at": row.last_submission_at,
                    "flags": flags,
                }
            )
        items.sort(key=lambda item: (-int(item["total"]), str(item["user"]["username"])))
        return {"period": {"from": period.start, "to": period.end}, "items": items}

    async def daily(self, period: Period, usernames: dict[uuid.UUID, str]) -> list[dict[str, Any]]:
        """Менеджер × день по Москве; `preliminary` — по самому дню, а не по всему периоду."""
        rows = await self.queries.counts_by_user_and_day(window_of(period, self.config.timezone), self.config.timezone)
        return [
            {
                "day": row.day,
                "user_id": row.user_id,
                "username": usernames.get(row.user_id, ""),
                **_stats(row.counts),
                "preliminary": self.preliminary(Period(row.day, row.day)),
            }
            for row in rows
        ]


def _stats(counts: Counts) -> dict[str, int]:
    return {
        "total": counts.total,
        "accepted": counts.accepted,
        "positive": counts.positive,
        "amended": counts.amended,
        "deleted": counts.deleted,
        "duplicate": counts.duplicate,
        "needs_review": counts.needs_review,
        "rejected": counts.rejected,
        "processing": counts.processing + counts.awaiting_outcome,
    }
