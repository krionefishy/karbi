import asyncio
import time
from collections import OrderedDict

# Telegram enforces two separate budgets on a bot: roughly 30 messages a second
# overall, and one a second into any single chat. The second one is the tighter
# constraint for a digest, which fans one event out across many chats.
DEFAULT_BOT_MESSAGES_PER_SECOND = 30.0
DEFAULT_CHAT_MESSAGES_PER_SECOND = 1.0
# Chats seen longer ago than this cannot be limiting anything any more.
_CHAT_MEMORY = 300.0
_MAX_REMEMBERED_CHATS = 10_000


class SendPacer:
    """Paces one bot's sends so Telegram never has a reason to answer 429.

    One instance per bot, held by that bot's delivery task: the limit is
    per-token, so pacing belongs exactly where the token is used. Waiting here
    costs nothing but wall-clock in a task that only serves this bot — which is
    the whole point of a task per bot.
    """

    def __init__(
        self,
        *,
        bot_messages_per_second: float = DEFAULT_BOT_MESSAGES_PER_SECOND,
        chat_messages_per_second: float = DEFAULT_CHAT_MESSAGES_PER_SECOND,
        clock=time.monotonic,
        sleep=asyncio.sleep,
    ) -> None:
        self._bot_interval = 1.0 / bot_messages_per_second if bot_messages_per_second > 0 else 0.0
        self._chat_interval = 1.0 / chat_messages_per_second if chat_messages_per_second > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        # Not 0.0: a fresh pacer must not hold the first send back, whatever
        # origin the clock happens to have.
        self._last_send = float("-inf")
        self._last_by_chat: OrderedDict[int, float] = OrderedDict()

    async def wait_turn(self, chat_id: int) -> float:
        """Sleep until this bot may send to this chat. Returns the seconds waited."""
        now = self._clock()
        ready_at = max(
            self._last_send + self._bot_interval,
            self._last_by_chat.get(chat_id, float("-inf")) + self._chat_interval,
        )
        delay = ready_at - now
        if delay > 0:
            await self._sleep(delay)
            now = ready_at
        else:
            delay = 0.0
        self._last_send = now
        self._last_by_chat[chat_id] = now
        self._last_by_chat.move_to_end(chat_id)
        self._forget_stale(now)
        return delay

    def _forget_stale(self, now: float) -> None:
        while self._last_by_chat:
            chat_id, seen_at = next(iter(self._last_by_chat.items()))
            if now - seen_at < _CHAT_MEMORY and len(self._last_by_chat) <= _MAX_REMEMBERED_CHATS:
                break
            del self._last_by_chat[chat_id]
