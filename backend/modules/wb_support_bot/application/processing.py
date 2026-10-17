"""Проход бота по кабинету: разобрать диалоги из зеркала и отправить то, что пора.

Тик идёт так: проверить, что отправлять можно (общий выключатель, свежесть
зеркала); спроецировать состояние каждого диалога; запустить кампании, у
которых вышла тишина; сверить «неизвестные» отправки с лентой; отправить
положенное. Часы, генератор случайных чисел и пауза передаются снаружи.
"""

import asyncio
import random
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from backend.modules.wb_core.application import CatalogMirror, ChatMirror
from backend.modules.wb_core.domain import CHAT_SENDER_SELLER, ChatEvent
from backend.modules.wb_core.infrastructure.wb.client import WBPermanentError, WBTemporaryError
from backend.modules.wb_support_bot.application.ports import ChatSender
from backend.modules.wb_support_bot.domain import (
    CampaignRules,
    DialogState,
    Pace,
    SendWindow,
    SentMessage,
    first_send_at,
    in_canary,
    next_send_at,
    project_dialog,
)
from backend.modules.wb_support_bot.infrastructure.postgres import (
    MODE_CANARY,
    MODE_LIVE,
    SEND_DRY_RUN,
    SEND_FAILED,
    SEND_SENDING,
    SEND_SENT,
    SEND_UNKNOWN,
    DialogKey,
    DialogModel,
    SendModel,
    SupportBotRepository,
    TemplateModel,
)

# «Неизвестную» отправку признаём неудавшейся, если лента за это время её не показала.
UNKNOWN_RESOLVE_AFTER = timedelta(minutes=45)
# После неудачи, которая не лечится повтором в ту же минуту, диалог ждёт столько.
RETRY_DELAY = timedelta(minutes=30)


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
    # Доля диалогов, по которым в режиме «канарейка» уходят настоящие сообщения.
    canary_percent: int = 10


@dataclass(slots=True)
class SellerReport:
    """Итог прохода по кабинету: что увидели и что сделали, и почему не отправляли."""

    dialogs: int = 0
    campaigns_started: int = 0
    sent: int = 0
    dry_run: int = 0
    failed: int = 0
    skipped: str | None = None
    notes: list[str] = field(default_factory=list)


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
        # Без каталога товары не проверяются на «снят с продажи»: нужен только тестам и ранним запускам.
        self.catalog = catalog
        self.rng = rng or random.Random()
        self.sleep = sleep

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

        stored = await self.repository.dialogs_by_keys(seller_id, since)
        journal = await self.repository.sends_by_dialog(seller_id, since)
        removed: set[str] = set()
        if self.catalog is not None:
            removed = await self.catalog.removed_articles(
                seller_id, {str(segment.nm_id) for segment in segments if segment.nm_id}
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
            model = await self._project(seller_id, segment, model, record, now, report, product_removed=gone)
            models[key] = model
            report.dialogs += 1
        await self.repository.retire_out_of_window(seller_id, since)
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
        await self._send_due(seller_id, tracked.mode, segments, now, report)
        return report

    # --- проекция ---------------------------------------------------------------

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
    ) -> DialogModel:
        created = model is None
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
        projection = project_dialog(
            segment.events,
            record,
            now=now,
            rules=self.policy.rules,
            campaign_started_at=model.campaign_started_at,
            campaigns_count=model.campaigns_count,
            product_removed=product_removed,
        )
        model.nm_id = segment.nm_id
        model.buyer_first_at = projection.buyer_first_at
        model.buyer_last_at = projection.buyer_last_at
        model.our_last_at = projection.our_last_at
        model.no_contact = model.no_contact or projection.no_contact
        model.attention = model.attention or projection.attention
        model.state = DialogState.CLOSED.value if model.closed else projection.state.value
        if projection.start_campaign:
            model.campaign_started_at = now
            model.campaigns_count += 1
            model.next_send_at = first_send_at(now, window=self.policy.window, pace=self.policy.pace, rng=self.rng)
            report.campaigns_started += 1
        elif projection.end_campaign:
            model.campaign_started_at = None
            model.next_send_at = None
        elif projection.state is not DialogState.IN_CAMPAIGN:
            model.next_send_at = None
        elif model.next_send_at is None:
            # Кампания идёт, а расписания нет (первый проход после сбоя): ставим первое сообщение.
            model.next_send_at = first_send_at(now, window=self.policy.window, pace=self.policy.pace, rng=self.rng)
        model.updated_at = now
        if created:
            await self.repository.add_dialog(model)
        return model

    # --- сверка «неизвестных» отправок ------------------------------------------

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

    # --- отправка ---------------------------------------------------------------

    async def _send_due(
        self, seller_id: uuid.UUID, mode: str, segments: list[_Segment], now: datetime, report: SellerReport
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
        signs: dict[str, str] = {}
        for segment in segments:
            for event in segment.events:
                if event.reply_sign:
                    signs[segment.chat_id] = event.reply_sign
        for dialog in due:
            live = self._is_live(mode, dialog)
            text_model = self._pick_template(templates, await self._last_template(dialog))
            sends = await self.repository.dialog_sends(_key(dialog))
            seq = (max((send.seq for send in sends), default=0)) + 1
            send = SendModel(
                seller_id=seller_id,
                chat_id=dialog.chat_id,
                prompt_at=dialog.prompt_at,
                seq=seq,
                template_id=text_model.id,
                text=text_model.text,
                status=SEND_SENDING if live else SEND_DRY_RUN,
                planned_at=now,
            )
            await self.repository.add_send(send)
            if not live:
                self._mark_sent(dialog, now)
                report.dry_run += 1
                await self.repository.session.commit()
                continue
            sign = signs.get(dialog.chat_id) or await self.chats.reply_sign(seller_id, dialog.chat_id)
            if not sign:
                send.status = SEND_FAILED
                send.error = "лента не отдала подпись чата"
                dialog.next_send_at = now + RETRY_DELAY
                report.failed += 1
                await self.repository.session.commit()
                continue
            # Строка со статусом sending зафиксирована до обращения к WB: после сбоя видно, что попытка была.
            await self.repository.session.commit()
            try:
                await self.sender.send(str(seller_id), sign, text_model.text)
            except WBPermanentError as error:
                send.status = SEND_FAILED
                send.error = str(error)
                dialog.next_send_at = now + RETRY_DELAY
                report.failed += 1
                if "доступа" in str(error) or "недействителен" in str(error):
                    await self.repository.set_paused_reason(seller_id, str(error))
                    await self.repository.session.commit()
                    report.skipped = f"кабинет остановлен: {error}"
                    return
            except WBTemporaryError as error:
                # Мог ли WB принять сообщение — неизвестно: сверка по ленте решит, повторять ли.
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

    def _is_live(self, mode: str, dialog: DialogModel) -> bool:
        """Уйдёт ли по диалогу настоящее сообщение: весь кабинет в live либо диалог попал в канарейку."""
        if mode == MODE_LIVE:
            return True
        if mode == MODE_CANARY:
            return in_canary(dialog.chat_id, dialog.prompt_at, self.policy.canary_percent)
        return False

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


def _key(dialog: DialogModel) -> DialogKey:
    return DialogKey(dialog.seller_id, dialog.chat_id, dialog.prompt_at)
