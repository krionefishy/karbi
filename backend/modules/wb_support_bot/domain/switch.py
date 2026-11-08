"""Итоговое решение «пишем ли по диалогу» и порядок, в каком его перебивают.

Тумблер диалога показывает это решение, а не отдельное сохранённое значение: пока
человек ничего не трогал, он повторяет вердикт модели. Одна функция служит и боту при
отправке, и странице, поэтому они не могут разойтись.

Порядок (первое сработавшее правило решает):
1. рассылки по диалогу нет (не идёт и не ждёт начала) или покупатель просил не писать;
2. товар выключен;
3. диалог исключён (человеком или ботом после отказов WB): тумблер можно включить обратно;
4. слой ответов ещё оценивает сообщение покупателя;
5. ручная отметка менеджера, пока не истёк срок и хвост диалога тот же;
6. вердикт модели.
"""

from dataclasses import dataclass
from datetime import datetime

from backend.modules.wb_support_bot.domain.dialog import DialogState
from backend.modules.wb_support_bot.domain.verdict import (
    MANUAL_ALLOW,
    MODE_ENFORCE,
    VERDICT_CLOSING,
    VERDICT_OPEN,
    VERDICT_UNCLEAR,
    may_send,
)

SOURCE_MODEL = "model"
SOURCE_MANUAL = "manual"
SOURCE_PRODUCT = "product"
SOURCE_EXCLUDED = "excluded"
SOURCE_SYSTEM = "system"

# Диалоги, по которым тумблер что-то значит: рассылка идёт или вот-вот начнётся.
SWITCHABLE_STATES = (DialogState.IN_CAMPAIGN.value, DialogState.AWAITING_BUYER.value)

REASON_STATE = "state"
REASON_NO_CONTACT = "no_contact"
REASON_PRODUCT_OFF = "product_off"
REASON_EXCLUDED = "excluded"
REASON_REPLY_HOLD = "reply_hold"
REASON_MANUAL_ON = "manual_on"
REASON_MANUAL_OFF = "manual_off"
REASON_VERDICT_OPEN = "verdict_open"
REASON_VERDICT_CLOSING = "verdict_closing"
REASON_VERDICT_UNCLEAR = "verdict_unclear"
REASON_VERDICT_PENDING = "verdict_pending"
REASON_MODEL_OFF = "model_off"


@dataclass(frozen=True, slots=True)
class Mailing:
    """`on` — будет ли бот писать; `source` и `reason` — почему; `locked` — тумблер нельзя переключить."""

    on: bool
    source: str
    reason: str
    locked: bool = False
    manual_until: datetime | None = None


def manual_valid(
    manual: str | None,
    *,
    manual_fingerprint: str | None,
    manual_until: datetime | None,
    tail_fingerprint: str | None,
    now: datetime,
) -> bool:
    """Решение менеджера действует, пока хвост тот же и срок не вышел (без срока — только по хвосту)."""
    if manual is None or manual_fingerprint != tail_fingerprint:
        return False
    return manual_until is None or manual_until > now


def decide(
    *,
    mode: str,
    state: str,
    no_contact: bool,
    excluded: bool,
    reply_hold: bool,
    product_enabled: bool,
    verdict: str | None,
    manual: str | None,
    manual_until: datetime | None,
) -> Mailing:
    """Итоговое решение по диалогу. `manual` — уже проверенная отметка (`None`, если её нет или она истекла)."""
    if state not in SWITCHABLE_STATES:
        return Mailing(False, SOURCE_SYSTEM, REASON_STATE, locked=True)
    if no_contact:
        return Mailing(False, SOURCE_SYSTEM, REASON_NO_CONTACT, locked=True)
    if not product_enabled:
        return Mailing(False, SOURCE_PRODUCT, REASON_PRODUCT_OFF, locked=True)
    if excluded:
        return Mailing(False, SOURCE_EXCLUDED, REASON_EXCLUDED)
    if reply_hold:
        return Mailing(False, SOURCE_SYSTEM, REASON_REPLY_HOLD, locked=True)
    if manual is not None:
        on = manual == MANUAL_ALLOW
        return Mailing(on, SOURCE_MANUAL, REASON_MANUAL_ON if on else REASON_MANUAL_OFF, manual_until=manual_until)
    on = may_send(mode, verdict, None)
    if mode != MODE_ENFORCE:
        return Mailing(on, SOURCE_MODEL, REASON_MODEL_OFF)
    if verdict == VERDICT_OPEN:
        return Mailing(True, SOURCE_MODEL, REASON_VERDICT_OPEN)
    if verdict == VERDICT_CLOSING:
        return Mailing(False, SOURCE_MODEL, REASON_VERDICT_CLOSING)
    if verdict == VERDICT_UNCLEAR:
        return Mailing(False, SOURCE_MODEL, REASON_VERDICT_UNCLEAR)
    return Mailing(False, SOURCE_MODEL, REASON_VERDICT_PENDING)
