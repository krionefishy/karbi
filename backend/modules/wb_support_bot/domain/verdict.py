# ruff: noqa: E501 — длинные строки только в тексте промпта, его не переносим.

import hashlib
import json
import re
from datetime import datetime

from backend.modules.wb_core.domain import CHAT_SENDER_CLIENT, CHAT_SENDER_SELLER, ChatEvent
from backend.modules.wb_support_bot.domain.dialog import SentMessage, _is_ours
from backend.modules.wb_support_bot.domain.reply import is_wb_notice

VERDICT_OPEN = "open"
VERDICT_CLOSING = "closing"
VERDICT_UNCLEAR = "unclear"
VERDICTS = (VERDICT_OPEN, VERDICT_CLOSING, VERDICT_UNCLEAR)

MANUAL_ALLOW = "allow"
MANUAL_DENY = "deny"

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ENFORCE = "enforce"

SYSTEM_PROMPT = """Ты помогаешь службе поддержки продавца на Wildberries. Тебе дают ОДНО сообщение продавца покупателю — последнее в чате. Контекста диалога нет, покупатель после этого сообщения молчит.

Определи по одному этому сообщению, завершает ли оно диалог с нашей стороны. Выбери один вердикт:

closing — сообщение закрывает вопрос: продавец сообщает о решении, одобрении возврата или компенсации, статусе заявки, благодарит, информирует, и от покупателя ничего не требуется.

open — сообщение ждёт реакции покупателя: в нём вопрос, просьба что-то прислать или сделать, предложение выбрать вариант решения, просьба уточнить или подтвердить.

unclear — по тексту нельзя понять, ждёт ли продавец ответа: текст пустой, обрезан, бессодержателен или двусмыслен.

Правила:
1. Решает последнее смысловое действие сообщения. Если после извинений есть вопрос или просьба, это open.
2. Вежливые формулы вроде «обращайтесь, если будут вопросы» не делают сообщение open.
3. Предложение вариантов («компенсация или возврат, сообщите, что удобнее») — это open.
4. Не додумывай то, чего нет в тексте. При сомнении выбирай unclear.
5. Отвечай только JSON.

Формат: {"verdict": "closing|open|unclear", "reason": "до 15 слов по-русски"}
"""

USER_PREFIX = "Сообщение продавца:\n"
REASON_LIMIT = 200


def tail_message(events: list[ChatEvent], sent: list[SentMessage], *, ignore_notices: bool = False) -> ChatEvent | None:
    """Последнее сообщение специалиста после первого ответа покупателя.

    Сообщения самого бота не считаются: они хвост не меняют. `None` — специалист
    не писал (или писал только бот): оценивать нечего.
    """
    ordered = sorted(events, key=lambda event: (event.added_at, event.event_id))
    if ignore_notices:
        ordered = [event for event in ordered if not is_wb_notice(event)]
    buyer_first: datetime | None = next(
        (event.added_at for event in ordered if event.sender == CHAT_SENDER_CLIENT), None
    )
    if buyer_first is None:
        return None
    human = [
        event
        for event in ordered
        if event.sender == CHAT_SENDER_SELLER
        and not event.review_prompt
        and event.added_at > buyer_first
        and not _is_ours(event, sent)
    ]
    return human[-1] if human else None


def tail_fingerprint(event: ChatEvent) -> str:
    """Отпечаток хвоста: то же сообщение даёт тот же отпечаток, правка или новое — другой."""
    raw = f"{event.event_id}|{(event.text or '').strip()}"
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()


def user_message(text: str) -> str:
    return USER_PREFIX + (text.strip() or "(без текста)")


def parse_verdict(raw: str) -> tuple[str, str]:
    """Вердикт и причина из ответа модели; всё непонятное — `unclear`, а не догадка."""
    found = re.search(r"\{.*\}", raw, re.DOTALL)
    if found is None:
        return VERDICT_UNCLEAR, "ответ модели без JSON"
    try:
        data = json.loads(found.group(0))
    except ValueError:
        return VERDICT_UNCLEAR, "ответ модели не разобран"
    if not isinstance(data, dict):
        return VERDICT_UNCLEAR, "ответ модели не разобран"
    verdict = str(data.get("verdict", "")).strip().lower()
    reason = str(data.get("reason", "")).strip()[:REASON_LIMIT]
    if verdict not in VERDICTS:
        return VERDICT_UNCLEAR, reason or "неизвестный вердикт модели"
    return verdict, reason


def may_send(mode: str, verdict: str | None, manual: str | None) -> bool:
    """Можно ли писать по диалогу. Вне режима `enforce` вердикт отправку не ограничивает.

    В `enforce`: ручная отметка менеджера (для того же хвоста) решает сама; иначе
    пишем только когда вердикт «ждём покупателя». Нет вердикта — не пишем: лучше
    промолчать, чем напомнить тому, кому вопрос уже закрыли.
    """
    if mode != MODE_ENFORCE:
        return True
    if manual == MANUAL_ALLOW:
        return True
    if manual == MANUAL_DENY:
        return False
    return verdict == VERDICT_OPEN
