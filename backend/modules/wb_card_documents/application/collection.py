import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
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
from backend.modules.wb_card_documents.application.stock import StockSource
from backend.modules.wb_card_documents.domain import (
    DEFAULT_THRESHOLDS,
    CardDocument,
    CardDocuments,
    CardState,
    DeclaredDocument,
    PreviousState,
    Verdict,
    alert_decision,
    evaluate,
)
from backend.modules.wb_card_documents.infrastructure.postgres import CardDocumentsRepository, CardStateModel
from backend.modules.wb_core.infrastructure.postgres import SellerRepository


class CardDocumentsSource(Protocol):
    async def cards(
        self, seller_id: str, *, start_cursor: tuple[datetime, int] | None = None
    ) -> list[CardDocuments]: ...


@dataclass(frozen=True, slots=True)
class CollectionResult:
    cards: int
    problems: int
    alerts: int
    closed: int
    baseline: bool
    skipped: bool = False
    summary_sent: bool = False
    full: bool = True


class CollectionService:
    """Перечитывает документы карточек кабинета, считает состояния и говорит о переходах.

    Сеть идёт до первого запроса к базе. Ошибка сбора ничего не пишет: прежние
    состояния остаются, ложных «ok» не бывает. Первый сбор молчит и шлёт одну сводку.
    Сообщения кладутся в outbox в той же транзакции, что и состояния.

    Первый сбор не считается состоявшимся, пока сводка не ушла: кабинет, подключённый
    до регистрации бота, остаётся в режиме первого сбора и получит сводку, как только бот
    появится. В журнал переходов `alerted` пишется по факту постановки сообщения.

    Обход двух видов. Полный — весь каталог: первый сбор, раз в `full_interval` и по
    кнопке; только он закрывает карточки, ушедшие из каталога. Между полными читаются
    только изменённые карточки — от курсора (`updatedAt` минус `cursor_margin`, настоящий
    `nmID` последней прочитанной карточки). Остальные
    карточки с документами пересчитываются из сохранённого: пороги истечения сдвигаются
    календарём, а не `updatedAt`.

    Состояния и журнал считаются по всем карточкам, а уведомления — только по карточкам с
    остатком не меньше `min_stock` или с неизвестным остатком (`alert_decision`). Эпизод,
    найденный при нулевом остатке, ждёт и уходит, когда остаток появится; `episode_alerted`
    и `alerted` в журнале пишутся по факту постановки сообщения.
    """

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        documents: CardDocumentsRepository,
        source: CardDocumentsSource,
        publisher: CardDocumentsPublisher,
        stocks: StockSource,
        *,
        timezone: ZoneInfo,
        thresholds: Sequence[int] = DEFAULT_THRESHOLDS,
        digest_time: str = "10:00",
        full_interval: timedelta = timedelta(hours=24),
        cursor_margin: timedelta = timedelta(seconds=60),
        min_stock: int = 1,
    ) -> None:
        self.session = session
        self.sellers = sellers
        self.documents = documents
        self.source = source
        self.publisher = publisher
        self.timezone = timezone
        self.thresholds = tuple(thresholds)
        self.digest_time = digest_time
        self.full_interval = full_interval
        self.cursor_margin = cursor_margin
        self.stocks = stocks
        self.min_stock = min_stock
        self.logger = logging.getLogger("wb.card_documents.collection")

    async def collect(
        self, seller_id: uuid.UUID, *, now: datetime | None = None, full: bool = False
    ) -> CollectionResult:
        """Temporary WB errors propagate: the worker retries the whole seller later.

        `full=True` forces the whole catalog (the «Обновить» button); otherwise the
        kind of pass follows the schedule.
        """
        moment = now or datetime.now(UTC)
        since = await self._changed_since(seller_id, moment, force_full=full)
        await self.session.rollback()
        cards = await self.source.cards(str(seller_id), start_cursor=since)
        today = moment.astimezone(self.timezone).date()
        incremental = since is not None

        tracked = await self.documents.still_tracked(seller_id)
        if tracked is None:
            await self.session.rollback()
            return CollectionResult(0, 0, 0, 0, baseline=False, skipped=True, full=not incremental)
        baseline = tracked.baseline_at is None
        previous = await self.documents.open_states(seller_id)
        seller = await self.sellers.get(seller_id)
        seller_name = seller.name if seller else "магазин"
        stock = await self.stocks.stock(seller_id, now=moment)

        fresh = {card.nm_id: card for card in cards}
        cursor = max(((card.updated_at, card.nm_id) for card in cards if card.updated_at is not None), default=None)
        unique = dict(fresh)
        if incremental:
            for nm_id, model in previous.items():
                if nm_id not in unique and model.documents:
                    unique[nm_id] = stored_card(model)
        rows: list[dict[str, Any]] = []
        transitions: list[dict[str, Any]] = []
        alerts: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
        problems = 0
        notifiable: list[dict[str, Any]] = []
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
            decision = alert_decision(_previous(before), state, stock=stock.get(card.nm_id), min_stock=self.min_stock)
            row["episode_alerted"] = decision.already
            if decision.send:
                notifiable.append(row)
            alert = not baseline and decision.send
            transition = None
            if alert or _changed(before, state):
                transition = self._transition(seller_id, card.nm_id, before, state, moment)
                transitions.append(transition)
            if alert:
                alerts.append((row, transition))

        closed = [] if incremental else [nm_id for nm_id in previous if nm_id not in unique]

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
                    "problems": len(notifiable),
                    "skipped_without_stock": problems - len(notifiable),
                    "digest_time": self.digest_time,
                },
            )
            for row in notifiable:
                row["episode_alerted"] = summary_sent
        else:
            for row, transition in alerts:
                queued = await self._alert(seller_id, seller_name, row, today)
                row["episode_alerted"] = queued
                if transition is not None:
                    transition["alerted"] = queued
                sent += int(queued)
        await self.documents.save_states(rows)
        await self.documents.close_states(seller_id, closed, moment)
        await self.documents.add_transitions(transitions)
        stored = (
            (tracked.cursor_updated_at, tracked.cursor_nm_id)
            if tracked.cursor_updated_at is not None and tracked.cursor_nm_id is not None
            else None
        )
        if stored is not None and (cursor is None or cursor < stored):
            cursor = stored
        await self.documents.finish_collection(
            seller_id, moment, baseline=summary_sent, full=not incremental, cursor=cursor
        )
        await self.session.commit()
        return CollectionResult(
            cards=len(fresh),
            problems=problems,
            alerts=sent,
            closed=len(closed),
            baseline=baseline,
            summary_sent=summary_sent,
            full=not incremental,
        )

    async def _changed_since(
        self, seller_id: uuid.UUID, now: datetime, *, force_full: bool
    ) -> tuple[datetime, int] | None:
        """Where a changed-cards read starts, or None when this pass must read the whole catalog.

        The start is the last card seen: its real nmID and its `updatedAt` moved back by the
        margin. Without a stored nmID the read is full: WB ignores a cursor with `nmID: 0`.
        """
        if force_full:
            return None
        tracked = await self.documents.tracked(seller_id)
        if (
            tracked is None
            or tracked.baseline_at is None
            or tracked.full_collected_at is None
            or tracked.cursor_updated_at is None
            or tracked.cursor_nm_id is None
            or tracked.full_collected_at <= now - self.full_interval
        ):
            return None
        return tracked.cursor_updated_at - self.cursor_margin, tracked.cursor_nm_id

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
    return PreviousState(model.state, model.reason, model.document_number, model.threshold, model.episode_alerted)


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


def stored_card(model: CardStateModel) -> CardDocuments:
    """A card rebuilt from what the last read stored, to recheck what the calendar moves."""
    documents = tuple(_stored_document(item) for item in model.documents or [] if isinstance(item, dict))
    declared = model.declared or {}
    overall = (
        Verdict(None, model.overall_status, model.overall_reason, model.overall_at)
        if model.overall_status is not None or model.overall_reason or model.overall_at
        else None
    )
    return CardDocuments(
        nm_id=model.nm_id,
        vendor_code=model.vendor_code,
        title=model.title,
        subject_name=model.subject_name,
        has_block=True,
        documents=documents,
        overall=overall,
        exclude_documents=False,
        declared=DeclaredDocument(
            number=declared.get("number"),
            start=_day(declared.get("start")),
            end=_day(declared.get("end")),
        ),
    )


def _stored_document(item: dict[str, Any]) -> CardDocument:
    has_verdict = any(item.get(key) is not None for key in ("verified", "status", "reason", "verdict_at"))
    verdict_at = item.get("verdict_at")
    return CardDocument(
        document_id=str(item["id"]) if item.get("id") is not None else None,
        document_type=item.get("type"),
        number=str(item.get("number") or ""),
        start=_day(item.get("start")),
        end=_day(item.get("end")),
        endless=bool(item.get("endless")),
        verdict=Verdict(
            item.get("verified"),
            item.get("status"),
            item.get("reason"),
            datetime.fromisoformat(verdict_at) if verdict_at else None,
        )
        if has_verdict
        else None,
    )


def _day(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
