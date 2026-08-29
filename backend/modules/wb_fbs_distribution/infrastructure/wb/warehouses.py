from backend.modules.wb_core.infrastructure.wb import WBJsonClient, WBPermanentError
from backend.modules.wb_fbs_distribution.infrastructure.wb.marketplace import MARKETPLACE_BUCKET


class WBFbsWarehouseWriter(WBJsonClient):
    """Команды, меняющие кабинет: создание, перепривязка и удаление складов.

    Отдельный класс от читающего клиента специально. Чтение справочника делает
    фоновая сверка каждые сутки; запись меняет чужой кабинет и запускается
    только руками оператора. Пока они в одном объекте, ничто не мешает сверке
    однажды вызвать не тот метод.
    """

    bucket = MARKETPLACE_BUCKET
    api_name = "WB Marketplace API"
    category = "Маркетплейс"

    async def create(self, seller_id: str, *, name: str, office_id: int) -> int:
        """Создать виртуальный склад под объектом WB. Возвращает `warehouseId`."""
        payload = await self.request(
            "POST",
            "/api/v3/warehouses",
            seller_id,
            json={"name": name, "officeId": office_id},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("id"), int):
            raise WBPermanentError(f"{self.api_name}: в ответе на создание склада нет id")
        return int(payload["id"])

    async def rename(self, seller_id: str, warehouse_id: int, *, name: str, office_id: int) -> None:
        """Изменить название или привязку склада.

        WB разрешает менять привязку не чаще раза в сутки, поэтому это отдельная
        команда оператора, а не то, что делает фоновая сверка при расхождении.
        """
        await self.request(
            "PUT",
            f"/api/v3/warehouses/{warehouse_id}",
            seller_id,
            json={"name": name, "officeId": office_id},
        )

    async def delete(self, seller_id: str, warehouse_id: int) -> None:
        """Удалить склад продавца. Необратимо."""
        await self.request("DELETE", f"/api/v3/warehouses/{warehouse_id}", seller_id)
