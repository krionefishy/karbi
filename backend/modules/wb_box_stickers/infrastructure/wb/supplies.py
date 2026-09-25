from typing import Any

from backend.modules.wb_box_stickers.domain import BoxLine, Package
from backend.modules.wb_core.infrastructure.wb import WBJsonClient, WBPermanentError

SUPPLIES_BUCKET = "supplies"


class WBSuppliesClient(WBJsonClient):
    """FBO-поставка и её упаковка из API поставок WB.

    Чужую поставку WB не отличает от ключа без категории: на обе отвечает 403.
    Поэтому `owns` не бросает ошибку доступа, а говорит «не моя» — кабинет
    ищется перебором, и отказ одного кабинета — нормальный ответ.
    """

    bucket = SUPPLIES_BUCKET
    api_name = "WB Supplies API"
    category = "Поставки"

    async def owns(self, seller_id: str, supply_id: int) -> bool:
        try:
            payload = await self.request("GET", f"/api/v1/supplies/{supply_id}", seller_id)
        except WBPermanentError:
            return False
        return isinstance(payload, dict)

    async def packages(self, seller_id: str, supply_id: int) -> list[Package]:
        payload = await self.request("GET", f"/api/v1/supplies/{supply_id}/package", seller_id)
        if payload is None:
            return []
        if not isinstance(payload, list):
            raise WBPermanentError(f"{self.api_name} вернул упаковку не списком")
        packages = []
        for raw in payload:
            package = self._package(raw)
            if package is not None:
                packages.append(package)
        return packages

    def _package(self, raw: Any) -> Package | None:
        if not isinstance(raw, dict):
            return None
        code = str(raw.get("packageCode") or "").strip()
        if not code:
            return None
        lines = []
        for item in raw.get("barcodes") or []:
            if not isinstance(item, dict):
                continue
            barcode = str(item.get("barcode") or "").strip()
            quantity = item.get("quantity")
            if barcode and isinstance(quantity, int) and quantity > 0:
                lines.append(BoxLine(barcode=barcode, quantity=quantity))
        total = raw.get("quantity")
        return Package(
            code=code,
            quantity=total if isinstance(total, int) else sum(line.quantity for line in lines),
            lines=tuple(lines),
        )
