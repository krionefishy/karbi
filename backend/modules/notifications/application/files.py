"""Порт скачивания входящих файлов для автоматизаций интерактивных ботов.

Автоматизация получает `file_ref` в событии `notifications.telegram.update.received` и забирает
байты через этот порт, не зная ни о релее, ни о его клиенте. Ошибки — доменные исключения
уведомлений: `MessengerFileTooLargeError` (больше лимита, файл не дочитан),
`MessengerPermanentError` (файла нет / выключено), `MessengerTemporaryError` (стоит повторить).
"""

from typing import Protocol

from backend.modules.notifications.infrastructure.relay import RelayClient
from backend.shared.settings import RelayConfig


class MessengerFiles(Protocol):
    async def fetch(self, bot_code: str, file_ref: str, *, max_bytes: int) -> bytes: ...


class RelayMessengerFiles:
    def __init__(self, client: RelayClient) -> None:
        self.client = client

    async def fetch(self, bot_code: str, file_ref: str, *, max_bytes: int) -> bytes:
        return await self.client.fetch_file(bot_code=bot_code, file_ref=file_ref, max_bytes=max_bytes)


def messenger_files(config: RelayConfig) -> MessengerFiles:
    return RelayMessengerFiles(RelayClient(config))
