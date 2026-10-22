import re
from datetime import UTC, date, datetime
from typing import Any

from backend.modules.wb_card_documents.domain import CardDocument, CardDocuments, DeclaredDocument, Verdict
from backend.modules.wb_core.infrastructure.wb import EgressGateway, WBContentClient

NUMBER_CHARACTERISTIC = 15001135
CERTIFICATE_NUMBER_CHARACTERISTIC = 15001136
START_CHARACTERISTIC = 15001137
END_CHARACTERISTIC = 15001138
NUMBER_NAMES = ("номер декларации", "номер сертификата")
START_NAMES = ("дата регистрации",)
END_NAMES = ("дата окончания действия",)


class WBCardDocumentsClient:
    """Карточки селлера с блоком `documents`, читанные тем же обходом, что и каталог.

    Пагинация и шлюз — у `WBContentClient`; здесь только разбор документов,
    вердиктов и характеристик декларации. Фото и описания не нужны и не хранятся.
    """

    def __init__(self, gateway: EgressGateway) -> None:
        self.catalog = WBContentClient(gateway)

    async def cards(self, seller_id: str, *, start_cursor: tuple[datetime, int] | None = None) -> list[CardDocuments]:
        """Весь каталог или, с `start_cursor` (`updatedAt`, настоящий `nmID`), только изменённые карточки."""
        cards = []
        async for raw in self.catalog.raw_cards(seller_id, start_cursor=start_cursor):
            parsed = parse_card_documents(raw)
            if parsed is not None:
                cards.append(parsed)
        return cards


def parse_card_documents(raw: Any) -> CardDocuments | None:
    """Tolerant by design: any field may be missing, the whole block included."""
    if not isinstance(raw, dict):
        return None
    nm_id = _int(raw.get("nmID"))
    if nm_id is None:
        return None
    raw_block = raw.get("documents")
    has_block = isinstance(raw_block, dict)
    block: dict[str, Any] = raw_block if isinstance(raw_block, dict) else {}
    items = block.get("items")
    documents = tuple(
        document for document in (parse_document(item) for item in items or [] if isinstance(item, dict)) if document
    )
    vendor_code = str(raw.get("vendorCode") or "")
    return CardDocuments(
        nm_id=nm_id,
        vendor_code=vendor_code,
        title=str(raw.get("title") or raw.get("subjectName") or vendor_code or nm_id),
        subject_name=str(raw.get("subjectName") or ""),
        has_block=has_block,
        documents=documents,
        overall=parse_verdict(block.get("overallVerdict")),
        exclude_documents=bool(block.get("excludeDocuments")),
        declared=parse_declared(raw.get("characteristics")),
        updated_at=_moment(raw.get("updatedAt")),
    )


def parse_document(raw: dict[str, Any]) -> CardDocument | None:
    number = str(raw.get("number") or "").strip()
    verdict = parse_verdict(raw.get("verdict"))
    if not number and verdict is None:
        return None
    return CardDocument(
        document_id=_text(raw.get("id")),
        document_type=_int(raw.get("type")),
        number=number,
        start=_iso_day(raw.get("startDate")),
        end=_iso_day(raw.get("endDate")),
        endless=raw.get("isEndless") is True,
        verdict=verdict,
    )


def parse_verdict(raw: Any) -> Verdict | None:
    if not isinstance(raw, dict):
        return None
    verified = raw.get("verified")
    reason = raw.get("reason")
    return Verdict(
        verified=verified if isinstance(verified, bool) else None,
        status=_int(raw.get("status")),
        reason=str(reason) if isinstance(reason, str) and reason else None,
        created_at=_moment(raw.get("createdAt")),
    )


def parse_declared(characteristics: Any) -> DeclaredDocument:
    """Номер и даты декларации из характеристик: по id, а у других предметов — по названию."""
    if not isinstance(characteristics, list):
        return DeclaredDocument()
    number: str | None = None
    start: date | None = None
    end: date | None = None
    for item in characteristics:
        if not isinstance(item, dict):
            continue
        value = _first_value(item.get("value"))
        if value is None:
            continue
        kind = _declared_kind(_int(item.get("id")), str(item.get("name") or "").strip().lower())
        if kind == NUMBER_CHARACTERISTIC:
            number = number or value
        elif kind == START_CHARACTERISTIC:
            start = start or _russian_day(value)
        elif kind == END_CHARACTERISTIC:
            end = end or _russian_day(value)
    return DeclaredDocument(number=number, start=start, end=end)


def _declared_kind(charc_id: int | None, name: str) -> int | None:
    """Номер декларации (тип 2) и номер сертификата (тип 1) — одно и то же для нашей сверки."""
    if charc_id == CERTIFICATE_NUMBER_CHARACTERISTIC:
        return NUMBER_CHARACTERISTIC
    if charc_id in (NUMBER_CHARACTERISTIC, START_CHARACTERISTIC, END_CHARACTERISTIC):
        return charc_id
    if name.startswith(NUMBER_NAMES):
        return NUMBER_CHARACTERISTIC
    if name.startswith(START_NAMES):
        return START_CHARACTERISTIC
    if name.startswith(END_NAMES):
        return END_CHARACTERISTIC
    return None


def _first_value(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            text = str(item).strip() if item is not None else ""
            if text:
                return text
        return None
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _russian_day(value: str) -> date | None:
    """`14.10.2026`; на случай другой записи принимаем и ISO."""
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", value.strip())
    if match:
        day, month, year = (int(part) for part in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            return None
    return _iso_day(value)


def _iso_day(value: Any) -> date | None:
    """Только дата, без перевода в другую зону: `2031-10-08T00:00:00Z` — это 8 октября."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = re.sub(r"(\.\d{6})\d+", r"\1", value.strip()).replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _text(value: Any) -> str | None:
    """Id документа у WB — строка UUID; число, если когда-то придёт, тоже станет строкой."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None
