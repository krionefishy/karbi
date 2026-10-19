import base64
import json
from datetime import datetime

from backend.modules.wb_box_stickers.domain import StoredPacking
from backend.storage.redis import RedisClient

KEY_PREFIX = "wb_box_stickers:packing:"
PACKING_TTL_SECONDS = 30 * 24 * 3600


class PackingStore:
    def __init__(self, redis: RedisClient) -> None:
        self.redis = redis

    async def save(self, packing: StoredPacking) -> None:
        payload = {
            "supply_id": packing.supply_id,
            "seller_id": packing.seller_id,
            "seller_name": packing.seller_name,
            "boxes": packing.boxes,
            "workbook": base64.b64encode(packing.workbook).decode("ascii"),
            "saved_at": packing.saved_at.isoformat(),
        }
        await self.redis.set(KEY_PREFIX + str(packing.supply_id), json.dumps(payload), ttl_seconds=PACKING_TTL_SECONDS)

    async def load(self, supply_id: int) -> StoredPacking | None:
        raw = await self.redis.get(KEY_PREFIX + str(supply_id))
        return self._parse(raw) if raw else None

    async def recent(self) -> list[StoredPacking]:
        """Все, что ещё не истекли, свежие первыми; их единицы — scan не страшен."""
        found: list[StoredPacking] = []
        for key in await self.redis.scan_iter(KEY_PREFIX + "*"):
            raw = await self.redis.get(key)
            packing = self._parse(raw) if raw else None
            if packing is not None:
                found.append(packing)
        found.sort(key=lambda packing: packing.saved_at, reverse=True)
        return found

    @staticmethod
    def _parse(raw: str) -> StoredPacking | None:
        try:
            payload = json.loads(raw)
            return StoredPacking(
                supply_id=int(payload["supply_id"]),
                seller_id=str(payload["seller_id"]),
                seller_name=str(payload["seller_name"]),
                boxes=int(payload["boxes"]),
                workbook=base64.b64decode(payload["workbook"]),
                saved_at=datetime.fromisoformat(payload["saved_at"]),
            )
        except (KeyError, ValueError, TypeError):
            return None
