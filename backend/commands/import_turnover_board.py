import argparse
import asyncio
import sys

from backend.modules.wb_core.infrastructure.postgres import SellerRepository
from backend.modules.wb_turnover.application.board_import import BoardFileError, SheetRow, normalize, parse_list
from backend.modules.wb_turnover.infrastructure.postgres import TurnoverRepository
from backend.shared.settings import load_settings
from backend.storage.pg import Database


def read_rows(path: str) -> list[SheetRow]:
    try:
        with open(path, "rb") as handle:
            return parse_list(handle.read())
    except BoardFileError as error:
        raise SystemExit(str(error)) from error


async def run(path: str, apply: bool) -> int:
    rows = read_rows(path)
    settings = load_settings()
    database = Database()
    await database.connect(settings.database.url, pool_size=1, max_overflow=0)
    failures = 0
    try:
        async with database.session() as session:
            sellers = await SellerRepository(session).list_sellers()
            turnover = TurnoverRepository(session)
            tracked = await turnover.tracked_seller_ids()
            for name in dict.fromkeys(row.seller for row in rows):
                wanted = [row for row in rows if row.seller == name]
                seller = next((item for item in sellers if normalize(item.name) == normalize(name)), None) or next(
                    (
                        item
                        for item in sellers
                        if normalize(name) in normalize(item.name) or normalize(item.name) in normalize(name)
                    ),
                    None,
                )
                if seller is None:
                    print(f"SKIP  «{name}»: селлер не найден ({len(wanted)} баркодов)")
                    failures += 1
                    continue
                if seller.id not in tracked:
                    print(f"SKIP  «{name}» → {seller.name}: не подключён к «Оборачиваемости» ({len(wanted)} баркодов)")
                    failures += 1
                    continue
                cards = await SellerRepository(session).list_barcode_cards(seller.id)
                unknown = [row.barcode for row in wanted if row.barcode not in cards]
                print(f"{name} → {seller.name}: {len(wanted)} баркодов, нет в каталоге: {len(unknown)}")
                for barcode in unknown:
                    print(f"  MISS  {barcode}")
                if not apply:
                    continue
                added = await turnover.add_board_items(seller.id, [row.barcode for row in wanted], None)
                for row in wanted:
                    await turnover.update_board_item(
                        seller.id, row.barcode, note=None, nomenclature=row.nomenclature[:500], updated_by=None
                    )
                await turnover.request_refresh(seller.id, None)
                await session.commit()
                print(f"  OK    добавлено {added}, сбор заказов поставлен в очередь")
    finally:
        await database.disconnect()
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Наполнить таблицу оборачиваемости из листа «ОСНОВА»")
    parser.add_argument("path")
    parser.add_argument("--apply", action="store_true", help="записать в базу, а не только показать")
    args = parser.parse_args()
    sys.exit(1 if asyncio.run(run(args.path, args.apply)) else 0)


if __name__ == "__main__":
    main()
