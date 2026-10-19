from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.modules.wb_core.domain import CHAT_SENDER_CLIENT, CHAT_SENDER_SELLER, CHAT_SOURCE_API, ChatEvent

GROUP_FOLLOWED = "followed"
GROUP_EARLY = "early"
GROUP_MISSED = "missed"
GROUP_BEFORE = "before"
GROUPS = (GROUP_FOLLOWED, GROUP_EARLY, GROUP_MISSED, GROUP_BEFORE)

DEFAULT_FOLLOW_UP_WITHIN = timedelta(minutes=15)

OUTCOME_REPLIED = "replied"
OUTCOME_SILENT = "silent"
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
    def reply_rate(self) -> float | None:
        """Доля ответивших от всех диалогов. Пока есть «ждём», она может только вырасти.

        Считать от диалогов с известным исходом нельзя: в свежий день, где ещё
        никто не успел промолчать, это давало бы 100 % при девяти ответах из
        девятнадцати.
        """
        return self.replied / self.total if self.total else None

    @property
    def silent_rate(self) -> float | None:
        return self.silent / self.total if self.total else None

    @property
    def pending_rate(self) -> float | None:
        return self.pending / self.total if self.total else None


def build_dialogs(
    events: Iterable[ChatEvent],
    *,
    since: datetime,
    until: datetime,
    window: timedelta,
    now: datetime,
    launch_at: datetime | None,
    follow_up_within: timedelta = DEFAULT_FOLLOW_UP_WITHIN,
) -> list[Dialog]:
    """Диалоги с автосообщением WB в `[since, until)` из событий, упорядоченных по чату и времени.

    `launch_at` — момент запуска рассылки: диалоги раньше него — группа «до
    запуска», что бы в них ни было. `follow_up_within` — не позже скольки после
    автосообщения сообщение из API считается нашим.
    """
    dialogs: list[Dialog] = []
    chat_id: str | None = None
    episode: list[ChatEvent] = []

    def close() -> None:
        if episode and since <= episode[0].added_at < until:
            dialogs.append(_dialog(episode, window=window, now=now, launch_at=launch_at, within=follow_up_within))

    for event in events:
        if event.chat_id != chat_id or event.review_prompt:
            close()
            chat_id, episode = event.chat_id, []
        if episode or event.review_prompt:
            episode.append(event)
    close()
    dialogs.sort(key=lambda dialog: dialog.prompt_at, reverse=True)
    return dialogs


def _dialog(
    episode: Sequence[ChatEvent], *, window: timedelta, now: datetime, launch_at: datetime | None, within: timedelta
) -> Dialog:
    prompt, rest = episode[0], episode[1:]
    first_client = next((event for event in rest if event.sender == CHAT_SENDER_CLIENT), None)
    follow_up = next(
        (
            event
            for event in rest
            if event.sender == CHAT_SENDER_SELLER
            and event.source == CHAT_SOURCE_API
            and event.added_at <= prompt.added_at + within
        ),
        None,
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
    if followed:
        group = GROUP_FOLLOWED
    elif launch_at is None or prompt.added_at < launch_at:
        group = GROUP_BEFORE
    elif reply is not None:
        group = GROUP_EARLY
    else:
        group = GROUP_MISSED
    return Dialog(
        chat_id=prompt.chat_id,
        prompt_at=prompt.added_at,
        nm_id=prompt.nm_id,
        group=group,
        outcome=outcome,
        anchor_at=anchor,
        follow_up_at=follow_up.added_at if follow_up else None,
        follow_up_text=follow_up.text if follow_up else None,
        follow_up_late=follow_up is not None and not followed,
        reply_at=reply.added_at if reply else None,
        reply_text=reply.text if reply else None,
        reply_has_attachments=bool(reply and reply.has_attachments),
    )


def summarize(dialogs: Iterable[Dialog], group: str | None = None) -> GroupSummary:
    """Итог по группе; без группы — по всем диалогам."""
    counts = {outcome: 0 for outcome in OUTCOMES}
    for dialog in dialogs:
        if group is None or dialog.group == group:
            counts[dialog.outcome] += 1
    return GroupSummary(
        total=sum(counts.values()),
        replied=counts[OUTCOME_REPLIED],
        silent=counts[OUTCOME_SILENT],
        pending=counts[OUTCOME_PENDING],
    )
