from collections.abc import Sequence

from backend.modules.wb_core.infrastructure.wb import WBJsonClient
from backend.modules.wb_fbs_distribution.infrastructure.wb.marketplace import MARKETPLACE_BUCKET, SKU_CHUNK


class WBFbsStockWriter(WBJsonClient):
    """Публикация остатков на виртуальных складах кабинета.

    Ключ записи — `sku`, то есть баркод, а не `chrtId`. Это не деталь вкуса:
    документация WB отдельно предупреждает, что имена полей не валидируются, и
    запрос с неверным именем вернёт успешные 204, ничего не изменив. Ошибка в
    этом месте не падает, а тихо перестаёт работать.
    """

    bucket = MARKETPLACE_BUCKET
    api_name = "WB Marketplace API"
    category = "Маркетплейс"

    async def publish(self, seller_id: str, warehouse_id: int, amounts: Sequence[tuple[str, int]]) -> int:
        """Записать остатки пачками. Возвращает число отправленных строк.

        Обнуление — это `amount = 0`, а не `DELETE`: тот удаляет саму запись
        остатка, назван документацией необратимым и имеет лимит на два порядка
        ниже. Для регулярной работы он не нужен.
        """
        if not amounts:
            return 0
        for offset in range(0, len(amounts), SKU_CHUNK):
            chunk = amounts[offset : offset + SKU_CHUNK]
            await self.request(
                "PUT",
                f"/api/v3/stocks/{warehouse_id}",
                seller_id,
                json={"stocks": [{"sku": sku, "amount": amount} for sku, amount in chunk]},
            )
        return len(amounts)
