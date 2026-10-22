import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_card_documents.application.notifications import (
    TEMPLATE_ALERT,
    TEMPLATE_BASELINE,
    CardDocumentsPublisher,
    alert_dedupe,
    card_params,
)
from backend.modules.wb_card_documents.domain import (
    DEFAULT_THRESHOLDS,
    CardDocuments,
    CardState,
    PreviousState,
    evaluate,
    should_alert,
)
from backend.modules.wb_card_documents.infrastructure.postgres import CardDocumentsRepository, CardStateModel
from backend.modules.wb_core.infrastructure.postgres import SellerRepository


class CardDocumentsSource(Protocol):
    async def cards(self, seller_id: str) -> list[CardDocuments]: ...


@dataclass(frozen=True, slots=True)
class CollectionResult:
    cards: int
    problems: int
    alerts: int
    closed: int
    baseline: bool
    skipped: bool = False
    summary_sent: bool = False


class CollectionService:
    """Перечитывает документы карточек кабинета, считает состояния и говорит о переходах.

    Сеть идёт до первого запроса к базе. Ошибка сбора ничего не пишет: прежние
    состояния остаются, ложных «ok» не бывает. Первый сбор молчит и шлёт одну сводку.
    Сообщения кладутся в outbox в той же транзакции, что и состояния.

    Первый сбор не считается состоявшимся, пока сводка не ушла: кабинет, подключённый
    до регистрации бота, остаётся в режиме первого сбора и получит сводку, как только бот
    появится. В журнал переходов `alerted` пишется по факту постановки сообщения.
    """

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        documents: CardDocumentsRepository,
        source: CardDocumentsSource,
        publisher: CardDocumentsPublisher,
        *,
        timezone: ZoneInfo,
        thresholds: Sequence[int] = DEFAULT_THRESHOLDS,
        digest_time: str = "10:00",
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.documents = documents
        self.source = source
        self.publisher = publisher
        self.timezone = timezone
        self.thresholds = tuple(thresholds)
        self.digest_time = digest_time
        self.logger = logging.getLogger("wb.card_documents.collection")

    async def collect(self, seller_id: uuid.UUID, *, now: datetime | None = None) -> CollectionResult:
        """Temporary WB errors propagate: the worker retries the whole seller later."""
        cards = await self.source.cards(str(seller_id))
        moment = now or datetime.now(UTC)
        today = moment.astimezone(self.timezone).date()

        tracked = await self.documents.still_tracked(seller_id)
        if tracked is None:
            await self.session.rollback()
            return CollectionResult(0, 0, 0, 0, baseline=False, skipped=True)
        baseline = tracked.baseline_at is None
        previous = await self.documents.open_states(seller_id)
        seller = await self.sellers.get(seller_id)
        seller_name = seller.name if seller else "магазин"

        unique = {card.nm_id: card for card in cards}
        rows: list[dict[str, Any]] = []
        transitions: list[dict[str, Any]] = []
        alerts: list[tuple[dict[str, Any], dict[str, Any]]] = []
        problems = 0
        for card in unique.values():
            state = evaluate(card, today, self.thresholds)
            before = previous.get(card.nm_id)
            if state.unknown_reason:
                self.logger.warning(
                    "card_documents_unknown_reason",
                    extra={"seller_id": str(seller_id), "nm_id": card.nm_id, "reason": state.reason},
                )
            problems += int(state.problem)
            row = self._row(seller_id, card, state, before, moment)
            rows.append(row)
            alert = not baseline and should_alert(_previous(before), state)
            if alert or _changed(before, state):
                transition = self._transition(seller_id, card.nm_id, before, state, moment)
                transitions.append(transition)
                if alert:
                    alerts.append((row, transition))

        closed = [nm_id for nm_id in previous if nm_id not in unique]
        await self.documents.save_states(rows)
        await self.documents.close_states(seller_id, closed, moment)

        sent = 0
        summary_sent = False
        if baseline:
            summary_sent = await self.publisher.to_seller(
                seller_id,
                TEMPLATE_BASELINE,
                dedupe=f"card-documents:baseline:{seller_id}:{tracked.enrolled_at.isoformat()}",
                params={
                    "seller_name": seller_name,
                    "cards": len(unique),
                    "problems": problems,
                    "digest_time": self.digest_time,
                },
            )
        else:
            for row, transition in alerts:
                transition["alerted"] = await self._alert(seller_id, seller_name, row, today)
                sent += int(transition["alerted"])
        await self.documents.add_transitions(transitions)
        await self.documents.finish_collection(seller_id, moment, baseline=summary_sent)
        await self.session.commit()
        return CollectionResult(
            cards=len(unique),
            problems=problems,
            alerts=sent,
            closed=len(closed),
            baseline=baseline,
            summary_sent=summary_sent,
        )

    async def _alert(self, seller_id: uuid.UUID, seller_name: str, row: dict[str, Any], today: date) -> bool:
        return await self.publisher.to_seller(
            seller_id,
            TEMPLATE_ALERT,
            dedupe=alert_dedupe(
                seller_id,
                row["nm_id"],
                row["state"],
                row["reason"],
                row["verdict_at"],
                row["threshold"],
                since=row["since"],
                document_number=row["document_number"],
                document_end=row["document_end"],
            ),
            params={"seller_name": seller_name, **card_params(row, today)},
        )

    @staticmethod
    def _row(
        seller_id: uuid.UUID,
        card: CardDocuments,
        state: CardState,
        before: CardStateModel | None,
        now: datetime,
    ) -> dict[str, Any]:
        document = state.document
        overall = card.overall
        same = before is not None and before.state == state.state and before.reason == state.reason
        return {
            "seller_id": seller_id,
            "nm_id": card.nm_id,
            "vendor_code": card.vendor_code[:255],
            "title": card.title[:512],
            "subject_name": card.subject_name[:255],
            "state": state.state,
            "reason": state.reason[:255] if state.reason else None,
            "document_number": document.number[:255] if document and document.number else None,
            "document_start": document.start if document else None,
            "document_end": document.end if document else None,
            "document_endless": bool(document and document.endless),
            "threshold": state.threshold,
            "verdict_at": state.verdict_at,
            "overall_status": overall.status if overall else None,
            "overall_reason": overall.reason[:255] if overall and overall.reason else None,
            "overall_at": overall.created_at if overall else None,
            "documents": [_document_json(item) for item in card.documents],
            "declared": {
                "number": card.declared.number,
                "start": card.declared.start.isoformat() if card.declared.start else None,
                "end": card.declared.end.isoformat() if card.declared.end else None,
            },
            "since": before.since if same and before is not None else now,
            "checked_at": now,
            "closed_at": None,
        }

    @staticmethod
    def _transition(
        seller_id: uuid.UUID,
        nm_id: int,
        before: CardStateModel | None,
        state: CardState,
        now: datetime,
    ) -> dict[str, Any]:
        return {
            "id": uuid.uuid4(),
            "seller_id": seller_id,
            "nm_id": nm_id,
            "from_state": before.state if before else None,
            "from_reason": before.reason if before else None,
            "to_state": state.state,
            "to_reason": state.reason[:255] if state.reason else None,
            "threshold": state.threshold,
            "document_number": state.document.number[:255] if state.document and state.document.number else None,
            "verdict_at": state.verdict_at,
            "alerted": False,
            "happened_at": now,
        }


def _previous(model: CardStateModel | None) -> PreviousState | None:
    if model is None:
        return None
    return PreviousState(model.state, model.reason, model.document_number, model.threshold)


def _changed(before: CardStateModel | None, state: CardState) -> bool:
    """В журнал — смена состояния, причины или порога; новая карточка — только с проблемой."""
    if before is None:
        return state.problem
    return (before.state, before.reason, before.threshold) != (state.state, state.reason, state.threshold)


def _document_json(document: Any) -> dict[str, Any]:
    verdict = document.verdict
    return {
        "id": document.document_id,
        "type": document.document_type,
        "number": document.number,
        "start": document.start.isoformat() if document.start else None,
        "end": document.end.isoformat() if document.end else None,
        "endless": document.endless,
        "verified": verdict.verified if verdict else None,
        "status": verdict.status if verdict else None,
        "reason": verdict.reason if verdict else None,
        "verdict_at": verdict.created_at.isoformat() if verdict and verdict.created_at else None,
    }
