"""Сверка статусов селлеров со шлюзом wb-egress.

Шлюз — источник правды о доставленных ключах и закреплённых IP; эта команда
переносит его состояние в колонки egress_* реестра. Используется как бэкфил
после выкатки саги и как ручная сверка при расхождениях.

Запуск на проде: docker compose exec api python -m backend.commands.sync_egress_status
"""

import asyncio
import sys
import uuid

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import EgressGateway
from backend.shared.settings import load_settings
from backend.storage.pg import Database


async def sync() -> int:
    settings = load_settings()
    gateway = EgressGateway(settings.egress)
    database = Database()
    await database.connect(settings.database.url, pool_size=1, max_overflow=0)
    matched = 0
    try:
        rows = await gateway.list_sellers()
        by_id = {row["seller_id"]: row for row in rows}
        async with database.session() as session:
            repository = SellerRepository(session)
            for seller in await repository.list_sellers(include_archived=True):
                remote = by_id.get(str(seller.id))
                if remote is None:
                    print(f"missing on egress: {seller.id}  {seller.name}")
                    continue
                await repository.set_egress_state(
                    uuid.UUID(str(seller.id)),
                    status=str(remote.get("status") or "undelivered"),
                    error=str(remote.get("verify_error") or "") or None,
                    ip=remote.get("egress_ip"),
                )
                matched += 1
                print(f"{remote.get('status'):<12} {remote.get('egress_ip') or '-':<16} {seller.id}  {seller.name}")
            await session.commit()
        print(f"synced {matched} sellers")
        return 0
    finally:
        await gateway.aclose()
        await database.disconnect()


if __name__ == "__main__":
    sys.exit(asyncio.run(sync()))
