import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from backend.modules.wb_core.application import ChatMirror, SellerNotFoundError
from backend.modules.wb_core.domain import CHAT_SENDER_CLIENT
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_support_bot.application.reply_stats import (
    EffectRow,
    ReplySummary,
    SliceRow,
    build_summary,
    open_escalations,
)
from backend.modules.wb_support_bot.application.reply_stats import (
    SendRow as StatSendRow,
)
from backend.modules.wb_support_bot.domain import (
    MANUAL_ALLOW,
    MANUAL_DENY,
    MODE_OFF,
    DialogState,
    Gate,
    is_wb_notice,
)
from backend.modules.wb_support_bot.infrastructure.postgres import (
    JOURNAL_STATUSES,
    MODE_CANARY,
    MODE_DRY_RUN,
    MODE_LIVE,
    DialogKey,
    DialogModel,
    ReplyVerdictModel,
    SendModel,
    SupportBotRepository,
    TemplateModel,
    VerdictModel,
)

MAX_TEMPLATE_LENGTH = 1000
MODES = (MODE_DRY_RUN, MODE_CANARY, MODE_LIVE)


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
    canary_percent: int
    classifier_mode: str
    reply_gate_mode: str


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
    verdicts: dict[tuple[str, str], VerdictModel] = field(default_factory=dict)
    replies: dict[str, ReplyVerdictModel] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReplyOverview:
    summary: ReplySummary
    generated_at: datetime
    live_since: datetime | None
    gate_mode: str


@dataclass(frozen=True, slots=True)
class EscalationRow:
    chat_id: str
    prompt_at: datetime
    nm_id: int | None
    category: str
    reason: str | None
    event_at: datetime
    state: str
    closed: bool
    excluded: bool


@dataclass(frozen=True, slots=True)
class ReplyMessage:
    at: datetime
    role: str
    text: str


@dataclass(frozen=True, slots=True)
class ReplyDialogCard:
    messages: tuple[ReplyMessage, ...]
    verdicts: tuple[ReplyVerdictModel, ...]


def _slice_row(row: ReplyVerdictModel) -> SliceRow:
    return SliceRow(
        chat_id=row.chat_id,
        prompt_at=row.prompt_at,
        first_event_at=row.first_event_at,
        last_event_at=row.last_event_at,
        status=row.status,
        category=row.category,
        guard=row.guard,
        requested_at=row.requested_at,
        completed_at=row.completed_at,
        model=row.model,
        tokens_in=row.tokens_in,
        tokens_out=row.tokens_out,
    )


def reply_action(row: ReplyVerdictModel) -> Gate:
    return _slice_row(row).gate


class ReplyStatsMixin:
    """Статистика ответов покупателей на рассылку и карточка диалога с текстом."""

    repository: SupportBotRepository
    chats: ChatMirror
    reply_prompt_version: str
    reply_gate_mode: str

    async def _tracked(self, seller_id: uuid.UUID):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def reply_overview(self, seller_id: uuid.UUID, *, days: int, now: datetime | None = None) -> ReplyOverview:
        await self._tracked(seller_id)
        if not 1 <= days <= 400:
            raise BotRequestError("Период должен быть от 1 до 400 дней")
        moment = now or datetime.now(UTC)
        since = moment - timedelta(days=days)
        slices = await self.repository.reply_slices(seller_id, self.reply_prompt_version, since)
        keys = {(row.chat_id, row.prompt_at) for row in slices}
        states = await self.repository.dialog_states(seller_id, keys)
        live_since = await self.repository.live_since(seller_id)
        summary = build_summary(
            days=days,
            now=moment,
            effect=[
                EffectRow(chat, prompt, first, live, reply)
                for chat, prompt, first, live, reply in await self.repository.effect_rows(seller_id, since)
            ],
            slices=[_slice_row(row) for row in slices],
            sends=[StatSendRow(*row) for row in await self.repository.sent_rows(seller_id, since)],
            dialogs={key: (model.state, model.closed) for key, model in states.items()},
            live_since=live_since,
        )
        return ReplyOverview(summary, moment, live_since, self.reply_gate_mode)

    async def reply_escalations(self, seller_id: uuid.UUID, *, days: int = 30) -> list[EscalationRow]:
        await self._tracked(seller_id)
        since = datetime.now(UTC) - timedelta(days=days)
        slices = await self.repository.reply_slices(seller_id, self.reply_prompt_version, since)
        states = await self.repository.dialog_states(seller_id, {(row.chat_id, row.prompt_at) for row in slices})
        latest = {(row.chat_id, row.prompt_at): row for row in slices}
        stats = [_slice_row(row) for row in slices]
        result: list[EscalationRow] = []
        for last in open_escalations(stats, {key: (model.state, model.closed) for key, model in states.items()}):
            key = (last.chat_id, last.prompt_at)
            dialog = states[key]
            result.append(
                EscalationRow(
                    chat_id=last.chat_id,
                    prompt_at=last.prompt_at,
                    nm_id=dialog.nm_id,
                    category=last.bucket,
                    reason=latest[key].reason,
                    event_at=last.last_event_at,
                    state=dialog.state,
                    closed=dialog.closed,
                    excluded=dialog.excluded,
                )
            )
        return result

    async def reply_dialog(self, seller_id: uuid.UUID, chat_id: str, prompt_at: datetime) -> ReplyDialogCard:
        await self._tracked(seller_id)
        key = DialogKey(seller_id, chat_id, prompt_at)
        if await self.repository.get_dialog(key) is None:
            raise BotRequestError("Диалог не найден")
        events = await self.chats.chat_events(
            seller_id, chat_id, since=prompt_at - timedelta(minutes=1), until=datetime.now(UTC) + timedelta(days=1)
        )
        reminders = {send.text.strip() for send in await self.repository.dialog_sends(key)}
        messages: list[ReplyMessage] = []
        for event in events:
            text = (event.text or "").strip()
            if event.review_prompt or is_wb_notice(event):
                role = "wb"
            elif event.sender == CHAT_SENDER_CLIENT:
                role = "buyer"
            elif text in reminders:
                role = "bot"
            else:
                role = "seller"
            messages.append(ReplyMessage(event.added_at, role, text))
        verdicts = await self.repository.dialog_reply_verdicts(key, self.reply_prompt_version)
        return ReplyDialogCard(tuple(messages), tuple(verdicts))


class SupportBotService(ReplyStatsMixin):
    """Что бот показывает оператору и что оператор ему может поручить."""

    def __init__(
        self,
        session: AsyncSession,
        sellers: SellerRepository,
        repository: SupportBotRepository,
        *,
        canary_percent: int = 10,
        classifier_mode: str = MODE_OFF,
        classifier_prompt_version: str = "v2",
        reply_gate_mode: str = MODE_OFF,
        reply_prompt_version: str = "r1",
    ) -> None:
        self.canary_percent = canary_percent
        self.classifier_mode = classifier_mode
        self.classifier_prompt_version = classifier_prompt_version
        self.reply_gate_mode = reply_gate_mode
        self.reply_prompt_version = reply_prompt_version
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
            canary_percent=self.canary_percent,
            classifier_mode=self.classifier_mode,
            reply_gate_mode=self.reply_gate_mode,
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
        verdicts = await self.repository.verdicts_by_tail(seller_id, self.classifier_prompt_version)
        replies = await self.repository.latest_reply_verdicts(
            seller_id, [row.chat_id for row in rows], self.reply_prompt_version
        )
        return DialogPage(tuple(rows), total, page, page_size, verdicts, replies)

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
        await self.repository.set_mode(seller_id, mode, self.canary_percent)
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

    async def set_manual_verdict(
        self, seller_id: uuid.UUID, chat_id: str, prompt_at: datetime, value: str | None
    ) -> None:
        """Решение менеджера по диалогу: `allow` — писать, `deny` — не писать, `None` — снять отметку.

        Действует только для текущего хвоста диалога: напишут снова — решение нужно принять заново.
        """
        await self._tracked(seller_id)
        if value not in (None, MANUAL_ALLOW, MANUAL_DENY):
            raise BotRequestError(f"Неизвестное решение: {value}")
        key = DialogKey(seller_id, chat_id, prompt_at)
        if not await self.repository.set_manual_verdict(key, value):
            raise BotRequestError("Диалог не найден или в нём ещё нет сообщения специалиста")
        await self.session.commit()
