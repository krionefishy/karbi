import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import ChatMirror, SellerNotFoundError
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_support_bot.domain import DialogState
from backend.modules.wb_support_bot.infrastructure.postgres import (
    JOURNAL_STATUSES,
    MODE_DRY_RUN,
    MODE_LIVE,
    DialogKey,
    DialogModel,
    SendModel,
    SupportBotRepository,
    TemplateModel,
)

MAX_TEMPLATE_LENGTH = 1000
MODES = (MODE_DRY_RUN, MODE_LIVE)


class NotEnrolledError(Exception):
    """Кабинет не подключён к боту."""


class BotRequestError(Exception):
    """Запрос, который нельзя выполнить: пустой текст, незнакомый режим или диалог."""


@dataclass(frozen=True, slots=True)
class SupportBotOverview:
    seller_count: int
    last_success_at: datetime | None
    failing: int
    paused: bool

    @property
    def status(self) -> str:
        if self.last_success_at is None:
            return "idle"
        if self.failing or self.paused:
            return "degraded"
        return "active"


@dataclass(frozen=True, slots=True)
class SellerBotView:
    seller_id: uuid.UUID
    seller_name: str
    mode: str
    paused_reason: str | None
    globally_paused: bool
    counts: dict[str, int]
    templates: tuple[TemplateModel, ...]
    mirror_error: str | None
    mirror_read_through: datetime | None
    dry_run: bool


@dataclass(frozen=True, slots=True)
class SendRow:
    send: SendModel
    nm_id: int | None


@dataclass(frozen=True, slots=True)
class SendPage:
    rows: tuple[SendRow, ...]
    total: int
    page: int
    page_size: int
    counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class DialogPage:
    dialogs: tuple[DialogModel, ...]
    total: int
    page: int
    page_size: int


class SupportBotService:
    """Что бот показывает оператору и что оператор ему может поручить."""

    def __init__(self, session: AsyncSession, sellers: SellerRepository, repository: SupportBotRepository) -> None:
        self.session = session
        self.sellers = sellers
        self.repository = repository
        self.chats = ChatMirror(session)

    async def overview(self) -> SupportBotOverview:
        enrolled = await self.repository.tracked_seller_ids() & {s.id for s in await self.sellers.list_sellers()}
        synced: list[datetime] = []
        failing = 0
        for seller_id in enrolled:
            state = await self.chats.state(seller_id)
            if state is None:
                continue
            failing += 1 if state.error else 0
            if state.synced_through is not None:
                synced.append(state.synced_through)
        return SupportBotOverview(
            seller_count=len(enrolled),
            last_success_at=max(synced) if synced else None,
            failing=failing,
            paused=await self.repository.is_paused(),
        )

    async def _tracked(self, seller_id: uuid.UUID):
        seller = await self.sellers.get(seller_id)
        if seller is None:
            raise SellerNotFoundError(seller_id)
        tracked = await self.repository.tracked(seller_id)
        if tracked is None:
            raise NotEnrolledError(seller_id)
        return seller, tracked

    async def view(self, seller_id: uuid.UUID) -> SellerBotView:
        seller, tracked = await self._tracked(seller_id)
        state = await self.chats.state(seller_id)
        return SellerBotView(
            seller_id=seller_id,
            seller_name=seller.name,
            mode=tracked.mode,
            paused_reason=tracked.paused_reason,
            globally_paused=await self.repository.is_paused(),
            counts=await self.repository.state_counts(seller_id),
            templates=tuple(await self.repository.templates(seller_id)),
            mirror_error=state.error if state else None,
            mirror_read_through=state.read_through if state else None,
            dry_run=tracked.mode == MODE_DRY_RUN,
        )

    async def dialogs(
        self, seller_id: uuid.UUID, *, state: str | None, flag: str | None, page: int, page_size: int
    ) -> DialogPage:
        await self._tracked(seller_id)
        if state is not None and state not in {item.value for item in DialogState}:
            raise BotRequestError(f"Неизвестное состояние диалога: {state}")
        if flag not in (None, "no_contact", "attention", "excluded"):
            raise BotRequestError(f"Неизвестный флаг: {flag}")
        rows, total = await self.repository.list_dialogs(
            seller_id, state=state, flag=flag, limit=page_size, offset=(page - 1) * page_size
        )
        return DialogPage(tuple(rows), total, page, page_size)

    async def sends(self, seller_id: uuid.UUID, *, status: str | None, page: int, page_size: int) -> SendPage:
        await self._tracked(seller_id)
        if status is not None and status not in JOURNAL_STATUSES:
            raise BotRequestError(f"Неизвестный статус отправки: {status}")
        rows, total = await self.repository.list_sends(
            seller_id, status=status, limit=page_size, offset=(page - 1) * page_size
        )
        return SendPage(
            rows=tuple(SendRow(send, nm_id) for send, nm_id in rows),
            total=total,
            page=page,
            page_size=page_size,
            counts=await self.repository.send_status_counts(seller_id),
        )

    async def set_mode(self, seller_id: uuid.UUID, mode: str) -> None:
        await self._tracked(seller_id)
        if mode not in MODES:
            raise BotRequestError(f"Неизвестный режим: {mode}")
        await self.repository.set_mode(seller_id, mode)
        await self.session.commit()

    async def set_paused(self, paused: bool) -> None:
        await self.repository.set_paused(paused)
        await self.session.commit()

    async def add_template(self, seller_id: uuid.UUID, text: str) -> TemplateModel:
        await self._tracked(seller_id)
        cleaned = self._clean_text(text)
        template = await self.repository.add_template(seller_id, cleaned)
        await self.session.commit()
        return template

    async def update_template_text(self, seller_id: uuid.UUID, template_id: int, text: str) -> TemplateModel:
        await self._tracked(seller_id)
        cleaned = self._clean_text(text)
        template = await self.repository.get_template(seller_id, template_id)
        if template is None:
            raise BotRequestError("Текст не найден")
        template.text = cleaned
        await self.session.commit()
        return template

    @staticmethod
    def _clean_text(text: str) -> str:
        cleaned = text.strip()
        if not cleaned:
            raise BotRequestError("Текст сообщения пустой")
        if len(cleaned) > MAX_TEMPLATE_LENGTH:
            raise BotRequestError(f"Текст длиннее {MAX_TEMPLATE_LENGTH} символов: WB его не примет")
        return cleaned

    async def set_template_active(self, seller_id: uuid.UUID, template_id: int, active: bool) -> TemplateModel:
        await self._tracked(seller_id)
        template = await self.repository.get_template(seller_id, template_id)
        if template is None:
            raise BotRequestError("Текст не найден")
        template.active = active
        await self.session.commit()
        return template

    async def delete_template(self, seller_id: uuid.UUID, template_id: int) -> None:
        await self._tracked(seller_id)
        if not await self.repository.delete_template(seller_id, template_id):
            raise BotRequestError("Текст не найден")
        await self.session.commit()

    async def set_dialog_flags(
        self,
        seller_id: uuid.UUID,
        chat_id: str,
        prompt_at: datetime,
        *,
        excluded: bool | None = None,
        closed: bool | None = None,
    ) -> None:
        await self._tracked(seller_id)
        key = DialogKey(seller_id, chat_id, prompt_at)
        if not await self.repository.set_dialog_flags(key, excluded=excluded, closed=closed):
            raise BotRequestError("Диалог не найден")
        await self.session.commit()
