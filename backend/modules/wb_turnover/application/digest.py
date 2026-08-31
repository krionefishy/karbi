import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotNotFoundError, BotRegistry
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_turnover.domain import STATUS_NO_STOCK, STATUS_OK
from backend.modules.wb_turnover.infrastructure.postgres import TurnoverRepository
from backend.shared.kafka_streams.topics import NotificationTopics
from backend.shared.outbox import OutboxRepository

TEMPLATE = "turnover.digest"


@dataclass(frozen=True, slots=True)
class DigestResult:
    seller_id: uuid.UUID
    articles: int
    sent: bool


class DigestService:
    """Every day, one message per seller listing what is about to run out.

    A message a day is the point — the shelf is still empty tomorrow — so there
    is no hysteresis here. What there is, is a log keyed by seller and day: a
    retry or a second manual run must not send the same digest twice.
    """

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        turnover: TurnoverRepository,
        bots: BotRegistry,
        *,
        threshold_days: int,
        bot_code: str,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.turnover = turnover
        self.bots = bots
        self.threshold_days = threshold_days
        self.bot_code = bot_code
        self.logger = logging.getLogger("wb.turnover.digest")

    @staticmethod
    def _number(value: int | None) -> int | None:
        return int(value) if value is not None else None

    async def send(self, seller_id: uuid.UUID, day: date) -> DigestResult:
        rows = await self.turnover.turnover_on(seller_id, day)
        # An article already at zero belongs in the digest more than any other:
        # losing the alert the moment the shelf empties would be exactly wrong.
        # But only if it was actually selling — «нет остатка и не заказывали ни
        # разу за окно» is the assortment tail, not news. For one seller that
        # split was 28 against 927.
        alerting = [
            row
            for row in rows
            if (row.status == STATUS_NO_STOCK and row.orders_count > 0)
            or (
                row.status == STATUS_OK
                and row.days_of_cover is not None
                and int(row.days_of_cover) < self.threshold_days
            )
        ]
        if not alerting:
            # Nothing to say. A daily "всё в порядке" teaches people to ignore
            # the bot, and then the real alert goes unread too.
            return DigestResult(seller_id, 0, sent=False)

        try:
            await self.bots.by_code(self.bot_code)
        except BotNotFoundError:
            # Without a registered bot nothing will ever be delivered. Logging
            # the day as sent would silently burn it, so leave no trace and let
            # a retry succeed once the bot is registered.
            self.logger.error("turnover_digest_bot_missing", extra={"seller_id": str(seller_id), "bot": self.bot_code})
            return DigestResult(seller_id, len(alerting), sent=False)

        seller = await self.sellers.get(seller_id)
        previous = {
            row.article: row.days_of_cover
            for row in await self.turnover.turnover_on(seller_id, day - timedelta(days=1))
        }
        names = {article.article: article.name for article in await self.sellers.list_articles(seller_id)}

        message_id = uuid.uuid4()
        if not await self.turnover.log_notification(seller_id, day, len(alerting), message_id):
            self.logger.info("turnover_digest_already_sent", extra={"seller_id": str(seller_id)})
            return DigestResult(seller_id, len(alerting), sent=False)

        OutboxRepository(self.session).add(
            aggregate_id=seller_id,
            event_type="TurnoverDigestRequested",
            topic=NotificationTopics.TELEGRAM_MESSAGE_REQUESTED,
            payload={
                "message_id": str(message_id),
                "bot": self.bot_code,
                "audience": {"type": "seller_subscribers", "seller_id": str(seller_id)},
                "template": TEMPLATE,
                "dedupe_key": f"turnover:{seller_id}:{day.isoformat()}",
                "params": {
                    "seller_name": seller.name if seller else "магазин",
                    "threshold_days": self.threshold_days,
                    "date": day.isoformat(),
                    "items": [
                        {
                            "article": row.article,
                            "name": names.get(row.article, ""),
                            "days": self._number(row.days_of_cover),
                            "previous_days": self._number(previous.get(row.article)),
                            "stock": row.stock_total,
                            "stock_fbo": row.stock_fbo,
                            "stock_fbs": row.stock_fbs,
                            "out_of_stock": row.status == STATUS_NO_STOCK,
                        }
                        for row in alerting
                    ],
                },
                "requested_at": datetime.now(UTC).isoformat(),
                "schema_version": 1,
            },
        )
        await self.session.commit()
        return DigestResult(seller_id, len(alerting), sent=True)
