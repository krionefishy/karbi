from typing import Protocol


class ChatSender(Protocol):
    """Отправка сообщения в чат с покупателем. Ошибки — `WBPermanentError` / `WBTemporaryError`."""

    async def send(self, seller_id: str, reply_sign: str, text: str) -> None: ...

    async def reply_signs(self, seller_id: str) -> dict[str, str]:
        """Подписи всех чатов кабинета (идентификатор чата → подпись) из списка чатов WB."""
        ...
