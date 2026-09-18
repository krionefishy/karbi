from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.modules.wb_core.domain import CHAT_SENDER_CLIENT, CHAT_SENDER_SELLER, CHAT_SOURCE_API, ChatEvent

# С нашим сообщением следом за автосообщением WB и без него. «Без» — это время
# до запуска рассылки, пропуски и покупатели, успевшие ответить раньше нас.
GROUP_FOLLOWED = "followed"
GROUP_BARE = "bare"
GROUPS = (GROUP_FOLLOWED, GROUP_BARE)

OUTCOME_REPLIED = "replied"
OUTCOME_SILENT = "silent"
# Окно ответа ещё не истекло: молчание пока ничего не значит.
OUTCOME_PENDING = "pending"
OUTCOMES = (OUTCOME_REPLIED, OUTCOME_SILENT, OUTCOME_PENDING)


@dataclass(frozen=True, slots=True)
class Dialog:
    """Один заход WB к покупателю после отзыва с низкой оценкой и что было дальше.

    Чат у покупателя с продавцом один, поэтому единица отчёта — не чат, а
    отрезок от автосообщения WB до следующего такого же. `anchor_at` — от чего
    ждём ответа: наше сообщение, а если его не было или покупатель успел
    раньше — само автосообщение.
    """

    chat_id: str
    prompt_at: datetime
    nm_id: int | None
    group: str
    outcome: str
    anchor_at: datetime
    follow_up_at: datetime | None
    follow_up_text: str | None
    # Наше сообщение ушло уже после ответа покупателя: на ответ оно не влияло.
    follow_up_late: bool
    reply_at: datetime | None
    reply_text: str | None
    reply_has_attachments: bool


@dataclass(frozen=True, slots=True)
class GroupSummary:
    total: int = 0
    replied: int = 0
    silent: int = 0
    pending: int = 0

    @property
    def decided(self) -> int:
        return self.replied + self.silent

    @property
    def reply_rate(self) -> float | None:
        """Доля ответивших среди диалогов с известным исходом; ожидающие в неё не входят."""
        return self.replied / self.decided if self.decided else None

    @property
    def silent_rate(self) -> float | None:
        return self.silent / self.decided if self.decided else None


def build_dialogs(
    events: Iterable[ChatEvent], *, since: datetime, until: datetime, window: timedelta, now: datetime
) -> list[Dialog]:
    """Диалоги с автосообщением WB в `[since, until)` из событий, упорядоченных по чату и времени."""
    dialogs: list[Dialog] = []
    chat_id: str | None = None
    episode: list[ChatEvent] = []

    def close() -> None:
        if episode and since <= episode[0].added_at < until:
            dialogs.append(_dialog(episode, window=window, now=now))

    for event in events:
        if event.chat_id != chat_id or event.review_prompt:
            close()
            chat_id, episode = event.chat_id, []
        # До первого автосообщения в чате — обычная переписка, к отчёту не относится.
        if episode or event.review_prompt:
            episode.append(event)
    close()
    dialogs.sort(key=lambda dialog: dialog.prompt_at, reverse=True)
    return dialogs


def _dialog(episode: Sequence[ChatEvent], *, window: timedelta, now: datetime) -> Dialog:
    prompt, rest = episode[0], episode[1:]
    first_client = next((event for event in rest if event.sender == CHAT_SENDER_CLIENT), None)
    follow_up = next(
        (event for event in rest if event.sender == CHAT_SENDER_SELLER and event.source == CHAT_SOURCE_API), None
    )
    followed = follow_up is not None and (first_client is None or first_client.added_at > follow_up.added_at)
    anchor = follow_up.added_at if followed and follow_up else prompt.added_at
    reply = next(
        (event for event in rest if event.sender == CHAT_SENDER_CLIENT and anchor < event.added_at <= anchor + window),
        None,
    )
    if reply is not None:
        outcome = OUTCOME_REPLIED
    elif now < anchor + window:
        outcome = OUTCOME_PENDING
    else:
        outcome = OUTCOME_SILENT
    return Dialog(
        chat_id=prompt.chat_id,
        prompt_at=prompt.added_at,
        nm_id=prompt.nm_id,
        group=GROUP_FOLLOWED if followed else GROUP_BARE,
        outcome=outcome,
        anchor_at=anchor,
        follow_up_at=follow_up.added_at if follow_up else None,
        follow_up_text=follow_up.text if follow_up else None,
        follow_up_late=follow_up is not None and not followed,
        reply_at=reply.added_at if reply else None,
        reply_text=reply.text if reply else None,
        reply_has_attachments=bool(reply and reply.has_attachments),
    )


def summarize(dialogs: Iterable[Dialog], group: str) -> GroupSummary:
    counts = {outcome: 0 for outcome in OUTCOMES}
    for dialog in dialogs:
        if dialog.group == group:
            counts[dialog.outcome] += 1
    return GroupSummary(
        total=sum(counts.values()),
        replied=counts[OUTCOME_REPLIED],
        silent=counts[OUTCOME_SILENT],
        pending=counts[OUTCOME_PENDING],
    )
