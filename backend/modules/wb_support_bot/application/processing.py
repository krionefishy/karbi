import asyncio
import random
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from backend.modules.wb_core.application import CatalogMirror, ChatMirror
from backend.modules.wb_core.domain import CHAT_SENDER_CLIENT, CHAT_SENDER_SELLER, ChatEvent
from backend.modules.wb_core.infrastructure.wb.client import WBPermanentError, WBTemporaryError
from backend.modules.wb_support_bot.application.mailing import mailing_for
from backend.modules.wb_support_bot.application.ports import ChatSender
from backend.modules.wb_support_bot.domain import (
    CATEGORY_FLAGS,
    MANUAL_ALLOW,
    MODE_ENFORCE,
    MODE_OFF,
    VERDICT_OPEN,
    CampaignRules,
    DialogState,
    Gate,
    Mailing,
    Pace,
    SendWindow,
    SentMessage,
    first_send_at,
    gate_decision,
    is_wb_notice,
    manual_valid,
    next_send_at,
    project_dialog,
    reply_slice,
    slice_fingerprint,
    slice_guard,
    tail_fingerprint,
    tail_message,
)
from backend.modules.wb_support_bot.infrastructure.postgres import (
    MODE_LIVE,
    NO_ARTICLE_KEY,
    SEND_FAILED,
    SEND_SENDING,
    SEND_SENT,
    SEND_UNKNOWN,
    VERDICT_DONE,
    VERDICT_PENDING,
    DialogKey,
    DialogModel,
    ReplyVerdictModel,
    SendModel,
    SupportBotRepository,
    TemplateModel,
    VerdictModel,
)
from backend.shared.kafka_streams.topics import SupportBotTopics
from backend.shared.outbox.repository import OutboxRepository

UNKNOWN_RESOLVE_AFTER = timedelta(minutes=45)
RETRY_DELAY = timedelta(minutes=30)
SLOW_RETRY_DELAY = timedelta(hours=6)
SLOW_RETRY_AFTER = 2
GIVE_UP_AFTER = 5
VERDICT_RECHECK = timedelta(minutes=10)
# После `classifier_max_attempts` неудач вердикт запрашивается раз в час, а не бросается навсегда:
# сбой модели не должен оставлять диалог без оценки до конца кампании.
VERDICT_SLOW_RETRY = timedelta(hours=1)
CAMPAIGN_START_WINDOW = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class BotPolicy:
    window: SendWindow = SendWindow()
    pace: Pace = Pace()
    rules: CampaignRules = CampaignRules()
    daily_cap: int = 400
    min_gap: timedelta = timedelta(seconds=3)
    batch: int = 20
    history: timedelta = timedelta(days=12)
    mirror_stale: timedelta = timedelta(minutes=90)
    classifier_mode: str = MODE_OFF
    classifier_prompt_version: str = "v2"
    classifier_retry: timedelta = timedelta(minutes=15)
    classifier_max_attempts: int = 3
    max_new_campaigns_per_hour: int = 6
    reply_gate_mode: str = MODE_OFF
    reply_prompt_version: str = "r3"
    reply_retry: timedelta = timedelta(minutes=15)
    reply_max_attempts: int = 10


@dataclass(slots=True)
class SellerReport:
    """Итог прохода по кабинету: что увидели и что сделали, и почему не отправляли."""

    dialogs: int = 0
    campaigns_started: int = 0
    sent: int = 0
    failed: int = 0
    held: int = 0
    verdicts_requested: int = 0
    replies_requested: int = 0
    replies_held: int = 0
    skipped: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _ReplyGate:
    """Итог слоя ответов по диалогу за проход: ждём вердикт (pending) или только что решили продолжать (released)."""

    pending: bool = False
    released: bool = False


@dataclass(frozen=True, slots=True)
class _Segment:
    chat_id: str
    prompt_at: datetime
    nm_id: int | None
    events: list[ChatEvent]


def split_dialogs(events: list[ChatEvent]) -> list[_Segment]:
    """Диалоги: отрезки чата от автосообщения WB до следующего такого же. События до первого — не наши."""
    by_chat: dict[str, list[ChatEvent]] = defaultdict(list)
    for event in events:
        by_chat[event.chat_id].append(event)
    segments: list[_Segment] = []
    for chat_id, chat_events in by_chat.items():
        current: list[ChatEvent] | None = None
        prompt: ChatEvent | None = None
        for event in sorted(chat_events, key=lambda item: (item.added_at, item.event_id)):
            if event.review_prompt:
                if prompt is not None and current is not None:
                    segments.append(_Segment(chat_id, prompt.added_at, prompt.nm_id, current))
                prompt, current = event, [event]
            elif current is not None:
                current.append(event)
        if prompt is not None and current is not None:
            segments.append(_Segment(chat_id, prompt.added_at, prompt.nm_id, current))
    return segments


class SupportBotProcessor:
    def __init__(
        self,
        repository: SupportBotRepository,
        chats: ChatMirror,
        sender: ChatSender,
        policy: BotPolicy,
        *,
        catalog: CatalogMirror | None = None,
        rng: random.Random | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.repository = repository
        self.chats = chats
        self.sender = sender
        self.policy = policy
        self.catalog = catalog
        self.rng = rng or random.Random()
        self.sleep = sleep
        # Ключи выключенных товаров кабинета на время прохода; у диалогов без артикула ключ 0.
        self._disabled: set[int] = set()
        # Вердикты по хвосту и остаток часового лимита стартов на время прохода по кабинету.
        self._verdicts: dict[tuple[str, str], VerdictModel] = {}
        self._start_budget = policy.max_new_campaigns_per_hour

    def _product_off(self, nm_id: int | None) -> bool:
        return (nm_id if nm_id is not None else NO_ARTICLE_KEY) in self._disabled

    async def process_seller(self, seller_id: uuid.UUID, *, now: datetime) -> SellerReport:
        report = SellerReport()
        tracked = await self.repository.tracked(seller_id)
        if tracked is None:
            report.skipped = "кабинет не подключён"
            return report
        mirror = await self.chats.state(seller_id)
        if mirror is None or mirror.read_through is None:
            report.skipped = "лента чатов ещё не читалась"
            return report
        fresh = now - mirror.read_through <= self.policy.mirror_stale
        since = now - self.policy.history
        events = await self.chats.review_dialog_events(seller_id, since=since, until=now + timedelta(days=1))
        segments = split_dialogs(events)

        self._disabled = await self.repository.disabled_products(seller_id)
        self._verdicts = (
            await self.repository.verdicts_by_tail(seller_id, self.policy.classifier_prompt_version)
            if self.policy.classifier_mode == MODE_ENFORCE
            else {}
        )
        started = await self.repository.campaigns_started_since(seller_id, now - CAMPAIGN_START_WINDOW)
        self._start_budget = max(0, self.policy.max_new_campaigns_per_hour - started)
        stored = await self.repository.dialogs_by_keys(seller_id, since)
        journal = await self.repository.sends_by_dialog(seller_id, since)
        removed: set[str] = set()
        if self.catalog is not None:
            removed = await self.catalog.removed_articles(
                seller_id, {str(segment.nm_id) for segment in segments if segment.nm_id}
            )
        reply_rows: dict[tuple[str, str], ReplyVerdictModel] = {}
        outbox = OutboxRepository(self.repository.session)
        if self.policy.reply_gate_mode != MODE_OFF:
            reply_rows = await self.repository.reply_verdicts_by_slice(
                seller_id, self.policy.reply_prompt_version, since
            )
        models: dict[tuple[str, datetime], DialogModel] = {}
        for segment in segments:
            key = (segment.chat_id, segment.prompt_at)
            model = stored.get(key)
            sends = journal.get(key, [])
            record = [
                SentMessage(send.sent_at or send.planned_at, send.text)
                for send in sends
                if send.status in (SEND_SENT, SEND_UNKNOWN)
            ]
            gone = segment.nm_id is not None and str(segment.nm_id) in removed
            gate = _ReplyGate()
            if self.policy.reply_gate_mode != MODE_OFF and model is not None:
                gate = self._reply_gate(seller_id, segment, model, reply_rows, outbox, now, report)
            model = await self._project(seller_id, segment, model, record, now, report, product_removed=gone, gate=gate)
            self._note_first_reply(model, segment, sends)
            models[key] = model
            report.dialogs += 1
        await self.repository.retire_out_of_window(seller_id, since)
        await self._request_verdicts(seller_id, models, now, report)
        await self.repository.session.commit()

        await self._reconcile(seller_id, segments, now)
        if tracked.paused_reason:
            report.skipped = f"кабинет остановлен: {tracked.paused_reason}"
            return report
        if await self.repository.is_paused():
            report.skipped = "рассылка остановлена общим выключателем"
            return report
        if not fresh:
            report.skipped = "зеркало чатов устарело, ничего не отправляем"
            return report
        if not self.policy.window.contains(now):
            report.skipped = "вне окна рассылки"
            return report
        if tracked.mode != MODE_LIVE:
            report.skipped = "рассылка кабинета не включена"
            return report
        await self._send_due(seller_id, segments, now, report)
        return report

    async def _project(
        self,
        seller_id: uuid.UUID,
        segment: _Segment,
        model: DialogModel | None,
        record: list[SentMessage],
        now: datetime,
        report: SellerReport,
        *,
        product_removed: bool = False,
        gate: _ReplyGate = _ReplyGate(),
    ) -> DialogModel:
        created = model is None
        enforce = self.policy.reply_gate_mode == MODE_ENFORCE
        if model is None:
            model = DialogModel(
                seller_id=seller_id,
                chat_id=segment.chat_id,
                prompt_at=segment.prompt_at,
                state=DialogState.NO_REPLY.value,
                campaigns_count=0,
                sent_count=0,
                no_contact=False,
                attention=False,
                excluded=False,
                closed=False,
            )
        model.nm_id = segment.nm_id
        tail = tail_message(segment.events, record, ignore_notices=enforce)
        model.tail_event_id = tail.event_id if tail else None
        model.tail_fingerprint = tail_fingerprint(tail) if tail else None
        if model.manual_verdict is not None and not manual_valid(
            model.manual_verdict,
            manual_fingerprint=model.manual_fingerprint,
            manual_until=model.manual_until,
            tail_fingerprint=model.tail_fingerprint,
            now=now,
        ):
            # Написали ещё раз или срок вышел: решение человека снято, тумблер снова следует за моделью.
            model.manual_verdict = None
            model.manual_fingerprint = None
            model.manual_until = None
        projection = project_dialog(
            segment.events,
            record,
            now=now,
            rules=self.policy.rules,
            campaign_started_at=model.campaign_started_at,
            product_removed=product_removed,
            cleared_through=model.reply_cleared_through if enforce else None,
            reply_pending=gate.pending,
            ignore_notices=enforce,
            can_start=self._can_start(model, now),
        )
        model.nm_id = segment.nm_id
        model.buyer_first_at = projection.buyer_first_at
        model.buyer_last_at = projection.buyer_last_at
        model.our_last_at = projection.our_last_at
        model.no_contact = model.no_contact or projection.no_contact
        model.attention = model.attention or projection.attention
        model.state = DialogState.CLOSED.value if model.closed else projection.state.value
        model.reply_hold = gate.pending and projection.state is DialogState.IN_CAMPAIGN
        if model.reply_hold:
            report.replies_held += 1
        if projection.start_campaign:
            model.campaign_started_at = now
            model.campaigns_count += 1
            self._start_budget -= 1
            model.next_send_at = first_send_at(now, window=self.policy.window, pace=self.policy.pace, rng=self.rng)
            report.campaigns_started += 1
        elif projection.end_campaign:
            model.campaign_started_at = None
            model.next_send_at = None
        elif projection.state is not DialogState.IN_CAMPAIGN:
            model.next_send_at = None
        elif model.next_send_at is None and not model.excluded:
            model.next_send_at = first_send_at(now, window=self.policy.window, pace=self.policy.pace, rng=self.rng)
        if gate.released and projection.state is DialogState.IN_CAMPAIGN and not projection.end_campaign:
            # Покупатель ответил, рассылка продолжается: следующее напоминание через обычный шаг, а не сразу.
            model.next_send_at = next_send_at(now, window=self.policy.window, pace=self.policy.pace, rng=self.rng)
        model.updated_at = now
        if created:
            await self.repository.add_dialog(model)
        return model

    def _can_start(self, model: DialogModel, now: datetime) -> bool:
        """Можно ли начать новую кампанию: товар включён, диалог не снят вручную, есть вердикт «ждём покупателя».

        В режиме enforce вердикт по текущему хвосту обязателен: без него (не пришёл, closing,
        unclear) диалог ждёт. Ручная отметка менеджера для того же хвоста сильнее вердикта.
        Часовой лимит стартов защищает покупателей от рассылки разом после долгой паузы.
        """
        if self._start_budget <= 0 or self._product_off(model.nm_id):
            return False
        if model.closed or model.excluded or model.no_contact:
            return False
        if self.policy.classifier_mode != MODE_ENFORCE:
            return True
        if model.manual_verdict is not None:
            return model.manual_verdict == MANUAL_ALLOW
        row = self._verdicts.get((model.chat_id, model.tail_fingerprint)) if model.tail_fingerprint else None
        return row is not None and row.status == VERDICT_DONE and row.verdict == VERDICT_OPEN

    def _reply_eligible(self, model: DialogModel, now: datetime) -> bool:
        """Слой ответов смотрит только диалоги, где рассылка действительно идёт."""
        return (
            model.campaign_started_at is not None
            and model.state == DialogState.IN_CAMPAIGN.value
            and not (model.closed or model.excluded or model.no_contact)
            and now - model.campaign_started_at < self.policy.rules.duration
        )

    def _reply_gate(
        self,
        seller_id: uuid.UUID,
        segment: _Segment,
        model: DialogModel,
        rows: dict[tuple[str, str], ReplyVerdictModel],
        outbox: OutboxRepository,
        now: datetime,
        report: SellerReport,
    ) -> _ReplyGate:
        """Продолжать ли рассылку после ответа покупателя.

        Срез — ещё не разобранные сообщения покупателя после старта кампании. По нему
        бот ставит запрос нейросети и не ждёт ответа. Пока вердикта нет, диалог держится
        в кампании без отправок: сбой модели рассылку не убивает, она истекает по сроку.
        """
        if not self._reply_eligible(model, now):
            return _ReplyGate()
        marks = [moment for moment in (model.campaign_started_at, model.reply_cleared_through) if moment is not None]
        events = reply_slice(segment.events, boundary=max(marks))
        if not events:
            return _ReplyGate()
        fingerprint = slice_fingerprint(events)
        key = (segment.chat_id, fingerprint)
        row = rows.get(key)
        policy = self.policy
        off = self._product_off(model.nm_id)
        if row is None and off:
            # Товар выключен: срез на оценку нейросети не отправляем, диалог держится до включения товара.
            return _ReplyGate(pending=True)
        if row is None:
            guard = slice_guard(events)
            row = ReplyVerdictModel(
                id=uuid.uuid4(),
                seller_id=seller_id,
                chat_id=segment.chat_id,
                prompt_at=segment.prompt_at,
                slice_fingerprint=fingerprint,
                first_event_id=events[0].event_id,
                last_event_id=events[-1].event_id,
                first_event_at=events[0].added_at,
                last_event_at=events[-1].added_at,
                messages=len(events),
                prompt_version=policy.reply_prompt_version,
                status=VERDICT_DONE if guard else VERDICT_PENDING,
                guard=guard,
                attempts=0 if guard else 1,
                requested_at=now,
                completed_at=now if guard else None,
            )
            self.repository.add_reply_verdict(row)
            rows[key] = row
            if not guard:
                self._request_reply(outbox, row, seller_id, report)
        elif (
            row.status != VERDICT_DONE
            and not off
            and row.attempts < policy.reply_max_attempts
            and (now - row.requested_at >= policy.reply_retry)
        ):
            row.status = VERDICT_PENDING
            row.error = None
            row.attempts += 1
            row.requested_at = now
            self._request_reply(outbox, row, seller_id, report)
        decision = gate_decision(guard=row.guard, status=row.status, category=row.category)
        if decision is Gate.WAIT:
            return _ReplyGate(pending=True)
        if decision is Gate.CONTINUE:
            model.reply_cleared_through = events[-1].added_at
            return _ReplyGate(released=True)
        flag = CATEGORY_FLAGS.get(row.category or "")
        if flag == "attention":
            model.attention = True
        elif flag == "no_contact":
            model.no_contact = True
        return _ReplyGate()

    @staticmethod
    def _request_reply(
        outbox: OutboxRepository, row: ReplyVerdictModel, seller_id: uuid.UUID, report: SellerReport
    ) -> None:
        outbox.add(
            aggregate_id=row.id,
            event_type="SupportBotReplyRequested",
            topic=SupportBotTopics.REPLY_REQUESTED,
            payload={"verdict_id": str(row.id), "seller_id": str(seller_id)},
        )
        report.replies_requested += 1

    @staticmethod
    def _note_first_reply(model: DialogModel, segment: _Segment, sends: list[SendModel]) -> None:
        """Когда покупатель впервые ответил после первого сообщения бота (живого или сухого)."""
        if model.reply_first_at is not None:
            return
        planned = [item.planned_at for item in sends if item.status in (SEND_SENT, SEND_UNKNOWN)]
        if not planned:
            return
        first_send = min(planned)
        replies = [
            event.added_at
            for event in segment.events
            if event.sender == CHAT_SENDER_CLIENT
            and not event.review_prompt
            and not is_wb_notice(event)
            and event.added_at > first_send
        ]
        if replies:
            model.reply_first_at = min(replies)

    async def _request_verdicts(
        self, seller_id: uuid.UUID, models: dict[tuple[str, datetime], DialogModel], now: datetime, report: SellerReport
    ) -> None:
        """Ставит запрос вердикта по диалогам в кампании и по ждущим новой кампании, а хвост ещё не оценён.

        Модель не вызывается здесь: бот только кладёт запрос в outbox и не ждёт ответа.
        Зависший или упавший запрос ставится заново: сначала через `classifier_retry`, после
        `classifier_max_attempts` неудач — раз в час.
        """
        if self.policy.classifier_mode == MODE_OFF:
            return
        version = self.policy.classifier_prompt_version
        existing = await self.repository.verdicts_by_tail(seller_id, version)
        outbox = OutboxRepository(self.repository.session)
        for model in models.values():
            if model.state not in (DialogState.IN_CAMPAIGN.value, DialogState.AWAITING_BUYER.value):
                continue
            if not model.tail_fingerprint or not model.tail_event_id:
                continue
            if model.excluded or model.closed or model.no_contact:
                continue
            if self._product_off(model.nm_id):
                continue
            row = existing.get((model.chat_id, model.tail_fingerprint))
            if row is None:
                row = VerdictModel(
                    id=uuid.uuid4(),
                    seller_id=seller_id,
                    chat_id=model.chat_id,
                    tail_event_id=model.tail_event_id,
                    tail_fingerprint=model.tail_fingerprint,
                    prompt_version=version,
                    status=VERDICT_PENDING,
                    attempts=1,
                    requested_at=now,
                )
                self.repository.add_verdict(row)
            elif row.status == VERDICT_DONE or now - row.requested_at < self._verdict_wait(row):
                continue
            else:
                row.status = VERDICT_PENDING
                row.error = None
                row.attempts += 1
                row.requested_at = now
            outbox.add(
                aggregate_id=row.id,
                event_type="SupportBotClassifyRequested",
                topic=SupportBotTopics.CLASSIFY_REQUESTED,
                payload={"verdict_id": str(row.id), "seller_id": str(seller_id)},
            )
            report.verdicts_requested += 1

    def _verdict_wait(self, row: VerdictModel) -> timedelta:
        """Пауза до нового запроса вердикта: короткая, пока попытки не исчерпаны, потом час."""
        if row.attempts < self.policy.classifier_max_attempts:
            return self.policy.classifier_retry
        return VERDICT_SLOW_RETRY

    def _mailing(self, dialog: DialogModel, verdicts: dict[tuple[str, str], VerdictModel], now: datetime) -> Mailing:
        """Итоговое решение по диалогу: вердикт нейросети, ручная отметка, тумблер товара и остановки бота."""
        row = verdicts.get((dialog.chat_id, dialog.tail_fingerprint)) if dialog.tail_fingerprint else None
        return mailing_for(
            dialog,
            row,
            mode=self.policy.classifier_mode,
            product_enabled=not self._product_off(dialog.nm_id),
            now=now,
        )

    async def _reconcile(self, seller_id: uuid.UUID, segments: list[_Segment], now: datetime) -> None:
        unresolved = await self.repository.unresolved_sends(seller_id)
        if not unresolved:
            return
        events_by_chat: dict[str, list[ChatEvent]] = defaultdict(list)
        for segment in segments:
            events_by_chat[segment.chat_id].extend(segment.events)
        for send in unresolved:
            found = next(
                (
                    event
                    for event in events_by_chat.get(send.chat_id, [])
                    if event.sender == CHAT_SENDER_SELLER
                    and not event.review_prompt
                    and (event.text or "").strip() == send.text.strip()
                    and abs(event.added_at - send.planned_at) <= timedelta(minutes=30)
                ),
                None,
            )
            if found is not None:
                send.status = SEND_SENT
                send.sent_at = found.added_at
                send.error = None
            elif now - send.planned_at >= UNKNOWN_RESOLVE_AFTER:
                send.status = SEND_FAILED
                send.error = "в ленте чата сообщения нет: считаем, что не ушло"
        await self.repository.session.commit()

    async def _send_due(
        self, seller_id: uuid.UUID, segments: list[_Segment], now: datetime, report: SellerReport
    ) -> None:
        local_midnight = now.astimezone(self.policy.window.timezone).replace(hour=0, minute=0, second=0, microsecond=0)
        spent = await self.repository.sent_today(seller_id, local_midnight.astimezone(UTC))
        room = self.policy.daily_cap - spent
        if room <= 0:
            report.skipped = "достигнут суточный потолок сообщений"
            return
        due = await self.repository.due_dialogs(
            seller_id, now=now, state=DialogState.IN_CAMPAIGN.value, limit=min(self.policy.batch, room)
        )
        if not due:
            return
        templates = await self.repository.templates(seller_id, active_only=True)
        if not templates:
            report.skipped = "у кабинета нет активных текстов"
            return
        fallback: dict[str, str] | None = None
        signs: dict[str, str] = {}
        for segment in segments:
            for event in segment.events:
                if event.reply_sign:
                    signs[segment.chat_id] = event.reply_sign
        verdicts: dict[tuple[str, str], VerdictModel] = {}
        if self.policy.classifier_mode == MODE_ENFORCE:
            verdicts = await self.repository.verdicts_by_tail(seller_id, self.policy.classifier_prompt_version)
        for dialog in due:
            if not self._mailing(dialog, verdicts, now).on:
                dialog.next_send_at = now + VERDICT_RECHECK
                report.held += 1
                await self.repository.session.commit()
                continue
            text_model = self._pick_template(templates, await self._last_template(dialog))
            sends = await self.repository.dialog_sends(_key(dialog))
            failures = _fail_streak(sends) + 1
            seq = (max((send.seq for send in sends), default=0)) + 1
            send = SendModel(
                seller_id=seller_id,
                chat_id=dialog.chat_id,
                prompt_at=dialog.prompt_at,
                seq=seq,
                template_id=text_model.id,
                text=text_model.text,
                status=SEND_SENDING,
                planned_at=now,
            )
            await self.repository.add_send(send)
            sign = signs.get(dialog.chat_id) or await self.chats.reply_sign(seller_id, dialog.chat_id)
            if not sign:
                if fallback is None:
                    fallback = await self._chat_list_signs(seller_id)
                sign = fallback.get(dialog.chat_id)
            if not sign:
                send.status = SEND_FAILED
                send.error = "ни лента, ни список чатов не отдали подпись чата"
                self._hold_after_failure(dialog, failures, now)
                report.failed += 1
                await self.repository.session.commit()
                continue
            await self.repository.session.commit()
            try:
                await self.sender.send(str(seller_id), sign, text_model.text)
            except WBPermanentError as error:
                send.status = SEND_FAILED
                send.error = str(error)
                self._hold_after_failure(dialog, failures, now)
                report.failed += 1
                if "доступа" in str(error) or "недействителен" in str(error):
                    await self.repository.set_paused_reason(seller_id, str(error))
                    await self.repository.session.commit()
                    report.skipped = f"кабинет остановлен: {error}"
                    return
            except WBTemporaryError as error:
                send.status = SEND_UNKNOWN
                send.error = str(error)
                dialog.next_send_at = now + UNKNOWN_RESOLVE_AFTER
                report.failed += 1
            else:
                send.status = SEND_SENT
                send.sent_at = now
                self._mark_sent(dialog, now)
                report.sent += 1
            await self.repository.session.commit()
            await self.sleep(self.policy.min_gap.total_seconds())

    @staticmethod
    def _hold_after_failure(dialog: DialogModel, failures: int, now: datetime) -> None:
        """Отступ по числу неудач подряд: короткий повтор, затем редкий, затем остановка диалога.

        WB умеет сутками отвечать отказом по одному чату, принимая остальные, а повтор раз
        в полчаса чаще обычного шага рассылки: без отступа один залипший диалог даёт десятки
        неудачных отправок и в одиночку красит мониторинг. После GIVE_UP_AFTER отказов подряд
        диалог выводим из рассылки и помечаем для менеджера — вернуть его может только человек.
        """
        if failures >= GIVE_UP_AFTER:
            dialog.next_send_at = None
            dialog.excluded = True
            dialog.attention = True
            return
        dialog.next_send_at = now + (SLOW_RETRY_DELAY if failures > SLOW_RETRY_AFTER else RETRY_DELAY)

    async def _chat_list_signs(self, seller_id: uuid.UUID) -> dict[str, str]:
        try:
            return await self.sender.reply_signs(str(seller_id))
        except (WBPermanentError, WBTemporaryError):
            return {}

    def _mark_sent(self, dialog: DialogModel, now: datetime) -> None:
        dialog.sent_count += 1
        dialog.last_sent_at = now
        dialog.our_last_at = now
        dialog.next_send_at = next_send_at(now, window=self.policy.window, pace=self.policy.pace, rng=self.rng)

    async def _last_template(self, dialog: DialogModel) -> int | None:
        sends = await self.repository.dialog_sends(_key(dialog))
        return sends[-1].template_id if sends else None

    def _pick_template(self, templates: list[TemplateModel], last_id: int | None) -> TemplateModel:
        """Случайный вариант, но не тот же, что в прошлый раз (если есть из чего выбирать)."""
        pool = [item for item in templates if item.id != last_id] or templates
        return self.rng.choice(pool)


def _fail_streak(sends: list[SendModel]) -> int:
    """Сколько последних отправок по диалогу подряд закончились неудачей."""
    streak = 0
    for send in reversed(sends):
        if send.status != SEND_FAILED:
            break
        streak += 1
    return streak


def _key(dialog: DialogModel) -> DialogKey:
    return DialogKey(dialog.seller_id, dialog.chat_id, dialog.prompt_at)
