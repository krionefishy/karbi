"""What can go wrong when a message leaves for the messenger.

Deliberately not named after any one messenger: this vocabulary is what the
dispatcher retries on, and it survives swapping Telegram for something else.
The relay translates the messenger's own wording into these three outcomes.
"""


class MessengerPermanentError(Exception):
    """The messenger will answer the same way next time: retrying is pointless."""


class MessengerTemporaryError(Exception):
    """Network trouble, an outage or a 5xx — the same call may well succeed later."""


class MessengerRateLimitError(MessengerTemporaryError):
    """The messenger asks to slow down; retry_after says for how long."""

    def __init__(self, description: str, retry_after: float | None = None) -> None:
        super().__init__(description)
        self.retry_after = retry_after
