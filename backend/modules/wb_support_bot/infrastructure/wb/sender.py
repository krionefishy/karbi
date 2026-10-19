from typing import Any

from backend.modules.wb_core.infrastructure.wb.client import WBPermanentError
from backend.modules.wb_core.infrastructure.wb.json_client import WBJsonClient

CHAT_BUCKET = "chat"
MESSAGE_PATH = "/api/v1/seller/message"
CHATS_PATH = "/api/v1/seller/chats"
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

    async def reply_signs(self, seller_id: str) -> dict[str, str]:
        """`GET /api/v1/seller/chats`: подпись каждого чата кабинета.

        Лента событий отдаёт подпись только у событий, собранных после миграции `wc017`,
        поэтому у давнего диалога подписи в зеркале может не быть. Список тяжёлый
        (у крупного кабинета больше мегабайта), так что вызывается редко и лишь когда без него не обойтись.
        """
        payload: Any = await self.request("GET", CHATS_PATH, seller_id)
        rows = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise WBPermanentError(f"{self.api_name}: список чатов пришёл в неожиданном виде")
        return {
            str(row["chatID"]): str(row["replySign"])
            for row in rows
            if isinstance(row, dict) and row.get("chatID") and row.get("replySign")
        }

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
            raise WBPermanentError(
                f"{self.api_name}: у ключа нет права записи или доступа к категории «{self.category}»: "
                "для отправки нужен токен с уровнем «Чтение и запись»"
            ) from error
        if isinstance(payload, dict) and payload.get("errors"):
            raise WBPermanentError(f"{self.api_name}: {payload['errors']}")
