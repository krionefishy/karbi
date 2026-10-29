import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from backend.modules.wb_card_documents.domain.entities import CardDocument, CardDocuments

STATE_REJECTED = "rejected"
STATE_MISSING = "missing"
STATE_EXPIRING = "expiring"
STATE_MISMATCH = "mismatch"
STATE_OK = "ok"
STATES = (STATE_REJECTED, STATE_MISSING, STATE_EXPIRING, STATE_MISMATCH, STATE_OK)
PROBLEMS = frozenset(STATES) - {STATE_OK}

REASON_VALID = "valid"
REASON_NOT_FOUND = "document_not_found"
REASON_INVALID = "documents_invalid"
REASON_MISSING = "documents_missing"
REASON_DOCUMENT_MISSING = "document_missing"
REASON_EXPIRED = "expired"
REASON_NUMBER_MISMATCH = "number_mismatch"
REASON_START_MISMATCH = "start_date_mismatch"
REASON_END_MISMATCH = "end_date_mismatch"
DOCUMENT_REASONS = frozenset(
    {
        REASON_DOCUMENT_MISSING,
        REASON_NOT_FOUND,
        "document_inactive",
        "document_expired",
        "applicant_mismatch",
        "trade_name_mismatch",
        "unknown",
        "document_type_mismatch",
        "document_dates_mismatch",
    }
)
LISTING_REASONS = frozenset(
    {
        REASON_INVALID,
        REASON_MISSING,
        "tnved_missing",
        "supplier_inn_missing",
        "supplier_not_registered",
        "supplier_inactive",
        "product_group_not_registered",
        "kiz_required",
        "kiz_certificate_missing",
    }
)
KNOWN_REASONS = frozenset({REASON_VALID}) | DOCUMENT_REASONS | LISTING_REASONS

STATUS_VALID = 1
STATUS_INVALID = 2
DEFAULT_THRESHOLDS = (30, 14, 7)
EXPIRED_THRESHOLD = 0
REASON_STATUS_INVALID = "status_2"


@dataclass(frozen=True, slots=True)
class CardState:
    """Where one card stands after a collection.

    `threshold` is the expiry threshold the nearest end date has crossed
    (30 / 14 / 7), set only for `expiring`. `unknown_reason` flags a reason we
    have no name for: it is kept as it came and counted as a problem.
    """

    state: str
    reason: str | None
    document: CardDocument | None
    verdict_at: datetime | None
    threshold: int | None = None
    unknown_reason: bool = False

    @property
    def problem(self) -> bool:
        return self.state in PROBLEMS


@dataclass(frozen=True, slots=True)
class PreviousState:
    """The stored state before this read; `alerted` — the current episode has already been sent."""

    state: str
    reason: str | None
    document_number: str | None
    threshold: int | None
    alerted: bool = False


@dataclass(frozen=True, slots=True)
class AlertDecision:
    """`send` — this read should notify; `already` — the episode was notified before and stays so."""

    send: bool
    already: bool = False


def rank(state: str) -> int:
    return STATES.index(state) if state in STATES else len(STATES)


def evaluate(card: CardDocuments, today: date, thresholds: Sequence[int] = DEFAULT_THRESHOLDS) -> CardState:
    """The card's state by the verdict of its documents, not of the card.

    Order of precedence: rejected, missing, expiring, mismatch, ok. The overall
    verdict is never a reason on its own while documents are present: it was
    seen stale («documents_missing» next to a valid document).
    """
    documents = card.documents
    if not documents:
        if _requires_document(card):
            overall = card.overall
            return CardState(STATE_MISSING, REASON_DOCUMENT_MISSING, None, overall.created_at if overall else None)
        return CardState(STATE_OK, None, None, None)

    for document in documents:
        problem = _document_problem(document)
        if problem is not None:
            reason, unknown = problem
            verdict_at = document.verdict.created_at if document.verdict else None
            return CardState(STATE_REJECTED, reason, document, verdict_at, unknown_reason=unknown)

    nearest = _nearest_expiry(documents)
    if nearest is not None:
        left = days_left(nearest, today)
        threshold = crossed_threshold(left, thresholds)
        if left is not None and threshold is not None:
            return CardState(
                STATE_EXPIRING,
                REASON_EXPIRED if left < 0 else None,
                nearest,
                _verdict_at(nearest),
                threshold=threshold,
            )

    mismatch = _mismatch(card)
    if mismatch is not None:
        reason, mismatched = mismatch
        return CardState(STATE_MISMATCH, reason, mismatched, _verdict_at(mismatched))

    main = documents[0]
    return CardState(STATE_OK, None, main, _verdict_at(main))


def should_alert(previous: PreviousState | None, current: CardState) -> bool:
    """A message goes out on a transition, never on a re-read of the same problem.

    New problem, a different reason, another document under the same rejection,
    or the next expiry threshold. Recovery is silent: the page shows it.
    """
    if not current.problem:
        return False
    if previous is None or previous.state != current.state:
        return True
    if current.state == STATE_EXPIRING:
        return previous.threshold is None or (current.threshold or 0) < previous.threshold
    if previous.reason != current.reason:
        return True
    number = current.document.number if current.document else None
    return previous.document_number != number


def stock_passes(stock: int | None, min_stock: int) -> bool:
    """Notify about a card with stock, or with stock unknown: a missed block costs more than a spare message."""
    return stock is None or stock >= min_stock


def alert_decision(
    previous: PreviousState | None, current: CardState, *, stock: int | None, min_stock: int
) -> AlertDecision:
    """Whether a problem card is to be announced now.

    An episode (problem + reason + document + expiry threshold, see `should_alert`) is
    announced once, at the first read where the card is alertable: has stock or the
    stock is unknown. A problem found at zero stock waits; when stock appears with the
    same problem, it goes out then. Stock falling back to zero changes nothing.
    """
    if not current.problem:
        return AlertDecision(False)
    if previous is not None and previous.alerted and not should_alert(previous, current):
        return AlertDecision(False, already=True)
    return AlertDecision(stock_passes(stock, min_stock))


def days_left(document: CardDocument, today: date) -> int | None:
    if document.endless or document.end is None:
        return None
    return (document.end - today).days


def crossed_threshold(left: int | None, thresholds: Sequence[int]) -> int | None:
    """The smallest threshold the remaining days have reached, or None while far away.

    An expired document is a threshold of its own (`EXPIRED_THRESHOLD`): «осталось 7 дней»
    and «срок истёк» are two messages, not one.
    """
    if left is None:
        return None
    if left < 0:
        return EXPIRED_THRESHOLD
    reached = [threshold for threshold in thresholds if left <= threshold]
    return min(reached) if reached else None


def normalize_number(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).upper()


def _requires_document(card: CardDocuments) -> bool:
    """Empty `items` is «missing» only when the seller declared a document WB does not see.

    That is a declaration or certificate number in the characteristics. The overall
    verdict `documents_missing` alone does not count: on Мунаева's cabinet 33 lamps,
    torches and steamers carry it with no document required of the seller (decision of
    22.10.2026). `excludeDocuments` is the seller confirming no documents are needed.
    """
    if card.exclude_documents:
        return False
    return card.declared.number is not None


def _document_problem(document: CardDocument) -> tuple[str, bool] | None:
    verdict = document.verdict
    if verdict is None:
        return None
    reason = verdict.reason
    if reason is not None and reason not in KNOWN_REASONS:
        return reason, True
    if verdict.status == STATUS_INVALID:
        return (reason if reason and reason != REASON_VALID else REASON_STATUS_INVALID), False
    if reason in KNOWN_REASONS and reason != REASON_VALID:
        return reason, False
    return None


def _nearest_expiry(documents: Sequence[CardDocument]) -> CardDocument | None:
    dated = [document for document in documents if not document.endless and document.end is not None]
    return min(dated, key=lambda document: document.end or date.max) if dated else None


def _mismatch(card: CardDocuments) -> tuple[str, CardDocument | None] | None:
    """Our own check: the characteristics against the documents WB holds.

    The number is compared first; dates only against the document carrying
    the same number, otherwise a second document would always «differ».
    """
    declared = card.declared
    if declared.empty:
        return None
    documents = card.documents
    if declared.number is not None:
        wanted = normalize_number(declared.number)
        matching = [document for document in documents if normalize_number(document.number) == wanted]
        if not matching:
            return REASON_NUMBER_MISMATCH, documents[0]
        document = matching[0]
    elif len(documents) == 1:
        document = documents[0]
    else:
        return None
    if declared.start is not None and document.start is not None and declared.start != document.start:
        return REASON_START_MISMATCH, document
    if declared.end is not None and document.end is not None and not document.endless and declared.end != document.end:
        return REASON_END_MISMATCH, document
    return None


def _verdict_at(document: CardDocument | None) -> datetime | None:
    if document is None or document.verdict is None:
        return None
    return document.verdict.created_at
