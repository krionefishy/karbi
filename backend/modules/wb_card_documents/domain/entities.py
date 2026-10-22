from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class Verdict:
    """WB's verdict on a document or on the card as a whole, as it came."""

    verified: bool | None
    status: int | None
    reason: str | None
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class CardDocument:
    document_id: int | None
    document_type: int | None
    number: str
    start: date | None
    end: date | None
    endless: bool
    verdict: Verdict | None


@dataclass(frozen=True, slots=True)
class DeclaredDocument:
    """What the seller typed into the card's characteristics: number and dates of the declaration."""

    number: str | None = None
    start: date | None = None
    end: date | None = None

    @property
    def empty(self) -> bool:
        return self.number is None and self.start is None and self.end is None


@dataclass(frozen=True, slots=True)
class CardDocuments:
    """One card as the documents automation needs it: identity, documents, verdicts.

    `has_block` is False when the content API returned no `documents` at all:
    then the card has nothing to check, which is not the same as «документ не загружен».
    """

    nm_id: int
    vendor_code: str
    title: str
    subject_name: str
    has_block: bool
    documents: tuple[CardDocument, ...]
    overall: Verdict | None
    exclude_documents: bool
    declared: DeclaredDocument = field(default_factory=DeclaredDocument)
