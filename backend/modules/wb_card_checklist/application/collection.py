import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_card_checklist.domain import PriceFacts, SubjectCharacteristic
from backend.modules.wb_card_checklist.infrastructure.postgres import ChecklistRepository
from backend.modules.wb_card_checklist.infrastructure.wb import WBCardClient, WBPricesClient
from backend.modules.wb_core.infrastructure.wb import WBPermanentError


@dataclass(frozen=True, slots=True)
class CollectionResult:
    cards: int
    prices: int | None
    subjects: int
    warning: str | None
    # Селлера отключили, пока читали WB: писать нечего и некуда.
    skipped: bool = False


class CollectionService:
    """Перечитывает карточки и цены селлера и досчитывает справочник характеристик.

    Сеть идёт до первого запроса к базе: транзакция не должна висеть открытой,
    пока шлюз разбирает очередь к WB.
    """

    def __init__(
        self,
        session: AsyncSession,
        checklist: ChecklistRepository,
        cards: WBCardClient,
        prices: WBPricesClient,
        *,
        subject_ttl: timedelta,
    ) -> None:
        self.session = session
        self.checklist = checklist
        self.cards = cards
        self.prices = prices
        self.subject_ttl = subject_ttl
        self.logger = logging.getLogger("wb.card_checklist.collection")

    async def collect(self, seller_id: uuid.UUID) -> CollectionResult:
        """Temporary WB errors propagate: the worker retries the whole seller later."""
        cards = await self.cards.cards(str(seller_id))
        warnings: list[str] = []
        prices: list[PriceFacts] | None
        try:
            prices = await self.prices.prices(str(seller_id))
        except WBPermanentError as error:
            # Ключ без категории «Цены и скидки» — не повод оставить чек-лист
            # без карточек: пункт скидки просто останется без данных.
            prices = None
            warnings.append(f"Цены не прочитаны: {error}")

        subject_ids = {card.subject_id for card in cards if card.subject_id is not None}
        stale = await self.checklist.stale_subjects(subject_ids, datetime.now(UTC) - self.subject_ttl)
        await self.session.commit()
        directory: dict[int, list[SubjectCharacteristic]] = {}
        for subject_id in sorted(stale):
            try:
                directory[subject_id] = await self.cards.characteristics(str(seller_id), subject_id)
            except WBPermanentError as error:
                self.logger.warning(
                    "subject_characteristics_unavailable", extra={"subject_id": subject_id, "error": str(error)}
                )
                warnings.append(f"Справочник предмета {subject_id} не прочитан: {error}")

        if not await self.checklist.still_tracked(seller_id):
            await self.session.rollback()
            return CollectionResult(0, None, 0, None, skipped=True)
        await self.checklist.replace_cards(seller_id, cards)
        if prices is not None:
            await self.checklist.replace_prices(seller_id, prices)
        for subject_id, characteristics in directory.items():
            # Пустой справочник — скорее сбой WB, чем предмет без характеристик:
            # сохранить его значило бы на неделю объявить все карточки полными.
            if characteristics:
                await self.checklist.save_subject(subject_id, characteristics)
        warning = "; ".join(warnings) or None
        await self.checklist.finish_collection(seller_id, warning)
        await self.session.commit()
        return CollectionResult(
            cards=len(cards),
            prices=len(prices) if prices is not None else None,
            subjects=len(directory),
            warning=warning,
        )
