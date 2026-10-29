from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from backend.modules.wb_core.domain import CHAT_SENDER_CLIENT, CHAT_SENDER_SELLER, ChatEvent
from backend.modules.wb_support_bot.domain import stopwords
from backend.modules.wb_support_bot.domain.reply import is_wb_notice

JOURNAL_MATCH_TOLERANCE = timedelta(minutes=15)


class DialogState(StrEnum):
    NO_REPLY = "no_reply"
    AWAITING_US = "awaiting_us"
    AWAITING_BUYER = "awaiting_buyer"
    IN_CAMPAIGN = "in_campaign"
    PRODUCT_REMOVED = "product_removed"
    EXHAUSTED = "exhausted"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class SentMessage:
    """Запись журнала: что бот отправил и когда."""

    sent_at: datetime
    text: str


@dataclass(frozen=True, slots=True)
class CampaignRules:
    silence: timedelta = timedelta(hours=4)
    duration: timedelta = timedelta(days=5)
    max_campaigns: int = 1
    max_idle: timedelta | None = None


@dataclass(frozen=True, slots=True)
class Projection:
    state: DialogState
    start_campaign: bool
    end_campaign: bool
    buyer_first_at: datetime | None
    buyer_last_at: datetime | None
    our_last_at: datetime | None
    human_last_at: datetime | None
    no_contact: bool
    attention: bool


def _is_ours(event: ChatEvent, sent: list[SentMessage]) -> bool:
    """Сообщение продавца, которое отправил бот: совпал текст и время с записью журнала."""
    text = (event.text or "").strip()
    return any(
        record.text.strip() == text and abs(record.sent_at - event.added_at) <= JOURNAL_MATCH_TOLERANCE
        for record in sent
    )


def project_dialog(
    events: list[ChatEvent],
    sent: list[SentMessage],
    *,
    now: datetime,
    rules: CampaignRules,
    campaign_started_at: datetime | None = None,
    campaigns_count: int = 0,
    product_removed: bool = False,
    cleared_through: datetime | None = None,
    reply_pending: bool = False,
    ignore_notices: bool = False,
) -> Projection:
    """Состояние диалога по его событиям, журналу бота и сохранённому состоянию кампании.

    `events` — события одного диалога (одного чата, от автосообщения WB до
    следующего), в любом порядке. `campaign_started_at` и `campaigns_count` —
    то, что модуль сам записал о кампании по этому диалогу раньше.

    Слой ответов: `cleared_through` — время последнего сообщения покупателя, по которому
    уже решено «рассылку продолжаем» (такие сообщения кампанию не останавливают);
    `reply_pending` — по новым сообщениям вердикта ещё нет, диалог остаётся в кампании, а
    отправки удерживает вызывающий код; `ignore_notices` — системные «Мы удалили это
    сообщение» не считать ни ответом покупателя, ни ответом продавца.
    """
    ordered = sorted(events, key=lambda event: event.added_at)
    if ignore_notices:
        ordered = [event for event in ordered if not is_wb_notice(event)]
    buyer = [event for event in ordered if event.sender == CHAT_SENDER_CLIENT]
    no_contact = any(stopwords.match(event.text).no_contact for event in buyer)
    attention = any(stopwords.match(event.text).attention for event in buyer)
    if not buyer:
        return Projection(
            DialogState.NO_REPLY, False, campaign_started_at is not None, None, None, None, None, no_contact, attention
        )
    buyer_first, buyer_last = buyer[0].added_at, buyer[-1].added_at
    # Для состояния важны только ещё не разобранные ответы.
    open_buyer = [event for event in buyer if cleared_through is None or event.added_at > cleared_through]
    if reply_pending and campaign_started_at is not None:
        open_buyer = [event for event in open_buyer if event.added_at <= campaign_started_at]
    buyer_open_last = open_buyer[-1].added_at if open_buyer else None
    ours = [
        event
        for event in ordered
        if event.sender == CHAT_SENDER_SELLER and not event.review_prompt and event.added_at > buyer_first
    ]
    human = [event for event in ours if not _is_ours(event, sent)]
    our_last = ours[-1].added_at if ours else None
    human_last = human[-1].added_at if human else None

    def result(state: DialogState, *, start: bool = False, end: bool = False) -> Projection:
        return Projection(state, start, end, buyer_first, buyer_last, our_last, human_last, no_contact, attention)

    def replied(moment: datetime | None) -> bool:
        return moment is not None and (our_last is None or moment > our_last)

    # Ответ покупателя после старта кампании её закрывает, даже если бот успел написать позже
    # (зеркало чатов отстаёт, и следующее напоминание уходит раньше, чем мы видим ответ).
    replied_in_campaign = (
        campaign_started_at is not None
        and buyer_open_last is not None
        and buyer_open_last > campaign_started_at
        and (human_last is None or human_last < buyer_open_last)
    )
    if our_last is None or replied(buyer_open_last) or replied_in_campaign:
        return result(DialogState.AWAITING_US, end=campaign_started_at is not None)
    anchor = human_last if human_last is not None else our_last
    idle = now - anchor
    stale = rules.max_idle is not None and idle > rules.max_idle
    if campaign_started_at is not None:
        if human_last is not None and human_last > campaign_started_at:
            return result(DialogState.AWAITING_BUYER, end=True)
        if stale or now - campaign_started_at >= rules.duration:
            return result(DialogState.EXHAUSTED)
        if product_removed:
            return result(DialogState.PRODUCT_REMOVED, end=True)
        return result(DialogState.IN_CAMPAIGN)
    if idle >= rules.silence and not stale and campaigns_count < rules.max_campaigns:
        if product_removed:
            return result(DialogState.PRODUCT_REMOVED)
        return result(DialogState.IN_CAMPAIGN, start=True)
    return result(DialogState.AWAITING_BUYER)
