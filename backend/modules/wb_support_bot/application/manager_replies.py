"""Когда в диалогах писал человек-менеджер: нужно, чтобы не считать нарушением стопа новую кампанию.

Рассылка может стартовать заново после ответа покупателя, если менеджер ему ответил и покупатель снова
замолчал. Такое сообщение бота идёт после ответа-стопа, но нарушением не является. Читаем ленту только по
диалогам-кандидатам (их единицы), а не по всем.
"""

import uuid
from datetime import datetime

from backend.modules.wb_core.application import ChatMirror
from backend.modules.wb_support_bot.application.reply_stats import ManagerReplies
from backend.modules.wb_support_bot.domain.decision import manager_message_times
from backend.modules.wb_support_bot.infrastructure.postgres import DialogKey, SupportBotRepository


async def manager_replies_for(
    chats: ChatMirror,
    repository: SupportBotRepository,
    seller_id: uuid.UUID,
    keys: set[tuple[str, datetime]],
    until: datetime,
) -> ManagerReplies:
    """Время сообщений менеджеров по диалогам `keys` (чат, автосообщение WB) от автосообщения до `until`."""
    result: dict[tuple[str, datetime], list[datetime]] = {}
    for chat_id, prompt_at in keys:
        events = await chats.chat_events(seller_id, chat_id, since=prompt_at, until=until)
        reminders = {
            send.text.strip() for send in await repository.dialog_sends(DialogKey(seller_id, chat_id, prompt_at))
        }
        times = manager_message_times(events, reminders)
        if times:
            result[(chat_id, prompt_at)] = times
    return result
