"""Сверка селлеров со шлюзом wb-egress — и статусов, и самого состояния.

Команда закрывает три вида расхождений:

- копирует статусы шлюза в колонки `egress_*` реестра (бэкфил после выкатки),
  по каждому маркетплейсу отдельно;
- гасит на шлюзе то, что должно быть погашено: архивный селлер, чей disable в
  момент архивации не доехал, и осиротевшие записи шлюза, чей селлер уже
  выкорчеван из реестра (purge при недоступном шлюзе);
- доносит переименование, застрявшее в статусе `unsynced`.

Запуск на проде: docker compose exec api python -m backend.commands.sync_egress_status
"""

import asyncio
import sys
import time
import uuid

from backend.modules.wb_core.domain import MARKETPLACE_WB
from backend.modules.wb_core.domain.entities import EGRESS_DISABLED, EGRESS_UNDELIVERED
from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_core.infrastructure.wb import EgressAdminError, EgressGateway
from backend.shared.settings import load_settings
from backend.storage.pg import Database


def _version() -> int:
    return time.time_ns() // 1_000_000


async def sync() -> int:
    settings = load_settings()
    gateway = EgressGateway(settings.egress)
    database = Database()
    await database.connect(settings.database.url, pool_size=1, max_overflow=0)
    failures = 0
    try:
        remote = {row["seller_id"]: row for row in await gateway.list_sellers()}
        async with database.session() as session:
            repository = SellerRepository(session)
            for seller in await repository.list_sellers(include_archived=True):
                row = remote.pop(str(seller.id), None)
                if row is None:
                    print(f"FAIL  отсутствует на шлюзе: {seller.id}  {seller.name}")
                    failures += 1
                    continue
                row = await _repair(gateway, seller, row)
                if row is None:
                    failures += 1
                    continue
                for marketplace, state in _states(row).items():
                    await repository.set_egress_state(
                        uuid.UUID(str(seller.id)),
                        status=state[0] or EGRESS_UNDELIVERED,
                        error=state[1],
                        ip=row.get("egress_ip"),
                        marketplace=marketplace,
                    )
                shown = " ".join(f"{name}={state[0] or '-'}" for name, state in sorted(_states(row).items()))
                print(f"{shown:<40} {row.get('egress_ip') or '-':<16} {seller.id}  {seller.name}")
            await session.commit()
        # Осиротевшие записи шлюза: селлер выкорчеван из реестра, а его ключ на
        # шлюзе остался живым (purge при недоступном шлюзе). Гасим.
        for seller_id, row in remote.items():
            if _all_disabled(row):
                continue
            try:
                await gateway.disable_seller(seller_id=seller_id, event_version=_version())
                print(f"disabled orphan: {seller_id}  {row.get('name')}")
            except EgressAdminError as error:
                print(f"FAIL  сирота не отключена: {seller_id}  {row.get('name')}: {error}")
                failures += 1
        print(f"done{f', провалов: {failures}' if failures else ''}")
        return 1 if failures else 0
    finally:
        await gateway.aclose()
        await database.disconnect()


async def _repair(gateway: EgressGateway, seller, row: dict) -> dict | None:
    """Донести локальное намерение до шлюза; вернуть его свежее состояние."""
    try:
        if seller.archived_at is not None and not _all_disabled(row):
            # Архивация прошла локально, disable не доехал — ключ всё ещё жив.
            # Хватает одной живой учётки: гасить надо все.
            return await gateway.disable_seller(seller_id=str(seller.id), event_version=_version())
        if seller.archived_at is None and row.get("name") != seller.name:
            # Переименование, застрявшее в unsynced.
            return await gateway.rename_seller(seller_id=str(seller.id), name=seller.name, event_version=_version())
    except EgressAdminError as error:
        print(f"FAIL  не удалось выровнять {seller.id}  {seller.name}: {error}")
        return None
    return row


def _states(row: dict) -> dict[str, tuple[str, str | None]]:
    """Статус и ошибка по каждому маркетплейсу, который шлюз знает про селлера.

    Шлюз до появления Ozon отдавал один плоский `status` — это был статус WB.
    Разбираем и его: сверку могут запустить, пока шлюз ещё не обновлён.
    """
    reported = row.get("marketplaces")
    if isinstance(reported, dict) and reported:
        return {
            name: (str(entry.get("status") or ""), str(entry.get("verify_error") or "") or None)
            for name, entry in reported.items()
            if isinstance(entry, dict)
        }
    return {MARKETPLACE_WB: (str(row.get("status") or ""), str(row.get("verify_error") or "") or None)}


def _all_disabled(row: dict) -> bool:
    """Селлер погашен только когда погашены все его учётки, а не одна из них."""
    return all(status == EGRESS_DISABLED for status, _ in _states(row).values())


if __name__ == "__main__":
    sys.exit(asyncio.run(sync()))
