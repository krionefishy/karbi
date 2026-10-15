from typing import Any

from backend.modules.wb_core.infrastructure.wb.client import WBPermanentError
from backend.modules.wb_core.infrastructure.wb.json_client import WBJsonClient

CHAT_BUCKET = "chat"
MESSAGE_PATH = "/api/v1/seller/message"
# Потолок WB на текст одного сообщения.
MAX_MESSAGE_LENGTH = 1000


class WBChatSender(WBJsonClient):
    """Отправка сообщения покупателю: `POST /api/v1/seller/message`.

    Запрос уходит одной попыткой: транспортный сбой после того, как WB принял
    сообщение, при повторе дал бы дубль покупателю. Что с сообщением стало после
    сбоя, выясняет сверка по ленте, а не повтор.
    """

    bucket = CHAT_BUCKET
    api_name = "WB Buyers Chat API"
    category = "Чат с покупателями"

    async def send(self, seller_id: str, reply_sign: str, text: str) -> None:
        if not text.strip():
            raise WBPermanentError(f"{self.api_name}: пустой текст сообщения")
        if len(text) > MAX_MESSAGE_LENGTH:
            raise WBPermanentError(f"{self.api_name}: текст длиннее {MAX_MESSAGE_LENGTH} символов")
        try:
            payload: Any = await self.request(
                "POST",
                MESSAGE_PATH,
                seller_id,
                form={"replySign": reply_sign, "message": text},
                attempts=1,
            )
        except WBPermanentError as error:
            if "доступа" not in str(error):
                raise
            # Чтение чатов у ключа при этом работает, поэтому дело почти наверняка в уровне доступа токена.
            raise WBPermanentError(
                f"{self.api_name}: у ключа нет права записи или доступа к категории «{self.category}»: "
                "для отправки нужен токен с уровнем «Чтение и запись»"
            ) from error
        if isinstance(payload, dict) and payload.get("errors"):
            raise WBPermanentError(f"{self.api_name}: {payload['errors']}")
