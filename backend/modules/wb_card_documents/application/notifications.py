import hashlib
import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.notifications.application import BotNotFoundError, BotRegistry
from backend.modules.wb_card_documents.domain import PROBLEMS, rank
from backend.modules.wb_card_documents.infrastructure.postgres import (
    CardDocumentsRepository,
    CardStateModel,
    TrackedSellerModel,
)
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.shared.kafka_streams.topics import NotificationTopics
from backend.shared.outbox import OutboxRepository

TEMPLATE_ALERT = "card_documents.alert"
TEMPLATE_BASELINE = "card_documents.baseline"
TEMPLATE_DIGEST = "card_documents.digest"
TEMPLATE_STALE = "card_documents.stale"
TEMPLATE_COLLECTION_ERROR = "card_documents.collection_error"

KIND_DIGEST = "digest"
KIND_STALE = "stale"
KIND_ERROR = "error"

EVENT_TYPE = "CardDocumentsNotificationRequested"


class CardDocumentsPublisher:
    """Кладёт сообщения бота документов в outbox; в мессенджер отсюда никто не ходит.

    Подписчикам кабинета — аудитория `seller_subscribers`, служебному чату — `chat`.
    Если бот ещё не заведён, сообщение не ставится: событие с неизвестным ботом
    модуль уведомлений всё равно отклонил бы.
    """

    def __init__(self, session: AsyncSession, bots: BotRegistry, *, bot_code: str, service_chat_id: int = 0) -> None:
        self.session = session
        self.bots = bots
        self.bot_code = bot_code
        self.service_chat_id = service_chat_id
        self.logger = logging.getLogger("wb.card_documents.notifications")
        self._bot_ready: bool | None = None

    async def ready(self) -> bool:
        if self._bot_ready is None:
            try:
                await self.bots.by_code(self.bot_code)
                self._bot_ready = True
            except BotNotFoundError:
                self.logger.error("card_documents_bot_missing", extra={"bot": self.bot_code})
                self._bot_ready = False
        return self._bot_ready

    async def to_seller(self, seller_id: uuid.UUID, template: str, *, dedupe: str, params: dict[str, Any]) -> bool:
        if not await self.ready():
            return False
        self._add(seller_id, {"type": "seller_subscribers", "seller_id": str(seller_id)}, template, dedupe, params)
        return True

    async def to_service(self, seller_id: uuid.UUID, template: str, *, dedupe: str, params: dict[str, Any]) -> bool:
        if not self.service_chat_id:
            self.logger.warning("card_documents_service_chat_missing", extra={"template": template, **params})
            return False
        if not await self.ready():
            return False
        self._add(seller_id, {"type": "chat", "chat_id": self.service_chat_id}, template, dedupe, params)
        return True

    def _add(self, seller_id: uuid.UUID, audience: dict[str, Any], template: str, dedupe: str, params: dict) -> None:
        OutboxRepository(self.session).add(
            aggregate_id=seller_id,
            event_type=EVENT_TYPE,
            topic=NotificationTopics.TELEGRAM_MESSAGE_REQUESTED,
            payload={
                "message_id": str(uuid.uuid4()),
                "bot": self.bot_code,
                "audience": audience,
                "template": template,
                "dedupe_key": dedupe,
                "params": params,
            },
        )


def alert_dedupe(
    seller_id: uuid.UUID,
    nm_id: int,
    state: str,
    reason: str | None,
    verdict_at: datetime | None,
    threshold: int | None,
    *,
    since: datetime | None,
    document_number: str | None = None,
    document_end: date | None = None,
) -> str:
    """seller + nmID + состояние, затем отпечаток эпизода.

    Уникальность ключа в очереди вечная, поэтому одного verdict.createdAt мало: WB не
    перепроверяет документ при правке характеристики, и mismatch → ok → mismatch дал бы
    тот же ключ, а второй алерт молча пропал бы. В отпечаток входят причина, время
    вердикта, начало эпизода (`since`: на перечитывании то же, на новом эпизоде новое),
    номер и срок документа и порог. Хеш держит длину ключа: причина WB бывает любой,
    а полный ключ с id бота и чатом обязан влезть в 255 символов.
    """
    parts = (
        reason or "-",
        _iso(verdict_at) or "-",
        _iso(since) or "-",
        document_number or "-",
        document_end.isoformat() if document_end else "-",
        "-" if threshold is None else str(threshold),
    )
    episode = hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]
    return f"card-documents:alert:{seller_id}:{nm_id}:{state}:{episode}"


def card_params(row: CardStateModel | Mapping[str, Any], today: date) -> dict[str, Any]:
    """Одна карточка для шаблона: из строки базы или из только что посчитанной строки."""
    get = row.get if isinstance(row, Mapping) else partial(getattr, row)
    end = get("document_end")
    return {
        "nm_id": get("nm_id"),
        "vendor_code": get("vendor_code"),
        "title": get("title"),
        "state": get("state"),
        "reason": get("reason"),
        "document_number": get("document_number"),
        "document_end": end.isoformat() if end else None,
        "days_left": remaining_days(end, bool(get("document_endless")), today),
    }


def remaining_days(end: date | None, endless: bool, today: date) -> int | None:
    if end is None or endless:
        return None
    return (end - today).days


@dataclass(frozen=True, slots=True)
class WatchReport:
    digest: bool = False
    stale: bool = False


class NotificationService:
    """Сообщения по расписанию: дайджест открытых проблем и служебное «наблюдение не работает»."""

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        documents: CardDocumentsRepository,
        publisher: CardDocumentsPublisher,
        *,
        timezone: ZoneInfo,
        digest_hour: int,
        digest_minute: int,
        stale_hours: int,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.documents = documents
        self.publisher = publisher
        self.timezone = timezone
        self.digest_hour = digest_hour
        self.digest_minute = digest_minute
        self.stale = timedelta(hours=stale_hours)

    async def watch(self, seller_id: uuid.UUID, *, now: datetime) -> WatchReport:
        tracked = await self.documents.tracked(seller_id)
        if tracked is None:
            return WatchReport()
        seller_name = await self._seller_name(seller_id)
        stale = await self._stale(seller_id, seller_name, tracked, now)
        digest = await self._digest(seller_id, seller_name, tracked.baseline_at, tracked.collected_at, now)
        await self.session.commit()
        return WatchReport(digest=digest, stale=stale)

    async def collection_failed(self, seller_id: uuid.UUID, error: str, *, now: datetime) -> bool:
        """Постоянная ошибка кабинета (ключ без «Контента» и т. п.) — один раз за эпизод, в служебный чат."""
        tracked = await self.documents.tracked(seller_id)
        if tracked is None:
            return False
        episode = _iso(tracked.collected_at) or f"never:{_iso(tracked.enrolled_at)}"
        key = f"{episode}:{hashlib.sha1(error.encode()).hexdigest()[:12]}"
        if await self.documents.logged(seller_id, KIND_ERROR, key):
            return False
        sent = await self.publisher.to_service(
            seller_id,
            TEMPLATE_COLLECTION_ERROR,
            dedupe=f"card-documents:error:{seller_id}:{key}",
            params={"seller_name": await self._seller_name(seller_id), "error": error[:500]},
        )
        await self.documents.log_sent(seller_id, KIND_ERROR, key, now)
        await self.session.commit()
        return sent

    def digest_due(self, now: datetime) -> bool:
        local = now.astimezone(self.timezone)
        return (local.hour, local.minute) >= (self.digest_hour, self.digest_minute)

    async def _digest(
        self,
        seller_id: uuid.UUID,
        seller_name: str,
        baseline_at: datetime | None,
        collected_at: datetime | None,
        now: datetime,
    ) -> bool:
        if baseline_at is None or not self.digest_due(now):
            return False
        day = now.astimezone(self.timezone).date()
        key = day.isoformat()
        if await self.documents.logged(seller_id, KIND_DIGEST, key):
            return False
        states = await self.documents.open_states(seller_id)
        problems = sorted(
            (row for row in states.values() if row.state in PROBLEMS),
            key=lambda row: (rank(row.state), row.vendor_code.lower(), row.nm_id),
        )
        sent = False
        if problems:
            sent = await self.publisher.to_seller(
                seller_id,
                TEMPLATE_DIGEST,
                dedupe=f"card-documents:digest:{seller_id}:{key}",
                params={
                    "seller_name": seller_name,
                    "date": key,
                    "collected_at": _iso(collected_at),
                    "stale": collected_at is None or collected_at < now - self.stale,
                    "items": [card_params(row, day) for row in problems],
                },
            )
        await self.documents.log_sent(seller_id, KIND_DIGEST, key, now)
        return sent

    async def _stale(self, seller_id: uuid.UUID, seller_name: str, tracked: TrackedSellerModel, now: datetime) -> bool:
        reference = tracked.collected_at or tracked.enrolled_at
        if reference >= now - self.stale:
            return False
        key = _iso(reference) or "never"
        if await self.documents.logged(seller_id, KIND_STALE, key):
            return False
        sent = await self.publisher.to_service(
            seller_id,
            TEMPLATE_STALE,
            dedupe=f"card-documents:stale:{seller_id}:{key}",
            params={
                "seller_name": seller_name,
                "collected_at": _iso(tracked.collected_at),
                "hours": int(self.stale.total_seconds() // 3600),
                "error": tracked.collection_error,
            },
        )
        await self.documents.log_sent(seller_id, KIND_STALE, key, now)
        return sent

    async def _seller_name(self, seller_id: uuid.UUID) -> str:
        seller = await self.sellers.get(seller_id)
        return seller.name if seller else "магазин"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
