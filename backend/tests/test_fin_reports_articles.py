"""Листы «По артикулам» и «Остатки»: формулы старого отчёта на его же цифрах и снимки остатков."""

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from backend.modules.fin_reports.application import fold_remains, last_closed_week_end
from backend.modules.fin_reports.domain import ARTICLE_COLUMNS, AdSpend, Stock, article_rows, stock_rows
from backend.modules.wb_core.domain import SalesReportTotals, WarehouseRemain
from backend.tests.test_fin_reports import DRILL, SAW, money, totals

MOSCOW = ZoneInfo("Europe/Moscow")
COLUMN = {title: take for title, take in ARTICLE_COLUMNS}


def drill_week() -> list[SalesReportTotals]:
    """Байбурин, неделя 39, бесщёточный шуруповёрт — цифры листа старого отчёта."""
    return [
        totals(
            doc_type_name="Продажа",
            quantity=736,
            gross=money("4519922.54"),
            retail_amount=money("2641718.83"),
            for_pay=money("2634882.01"),
            acquiring_fee=money("102786.23"),
            cashback_amount=money("69620"),
            cashback_commission_change=money("3254.32"),
        ),
        totals(
            doc_type_name="Возврат",
            quantity=5,
            gross=money("29259"),
            retail_amount=money("17985"),
            for_pay=money("17985"),
        ),
        totals(
            seller_oper_name="Логистика",
            bonus_type_name="К клиенту при продаже",
            delivery_amount=822,
            delivery_service=money("158221"),
        ),
        totals(
            seller_oper_name="Логистика",
            bonus_type_name="От клиента при отмене",
            return_amount=86,
            delivery_service=money("10301"),
        ),
        totals(seller_oper_name="Логистика", bonus_type_name="Перевозка", delivery_service=money("1073.18")),
        totals(seller_oper_name="Штраф", penalty=money("213.19")),
        totals(seller_oper_name="Хранение", paid_storage=money("13.49")),
    ]


def test_the_article_row_reproduces_the_old_sheet_column_by_column() -> None:
    [row] = article_rows(
        drill_week(),
        cost_of=lambda nm_id: money(1526),
        stock_of=lambda nm_id, size: Stock(in_warehouse=321, to_client=583, from_client=100, total=486),
        ads_of=lambda nm_id: AdSpend(balance=money(281428), promotion_info=money(260009)),
        tax_rate=Decimal("0.12"),
    )

    def column(title: str) -> Any:
        value = COLUMN[title](row)
        return value.quantize(Decimal("0.01")) if isinstance(value, Decimal) else value

    assert (row.deliveries, row.refusals, row.sales, row.returns, row.realized) == (822, 86, 736, 5, 731)
    assert column("% выкупа") == Decimal("0.89")
    assert column("Вся стоимость реализованного товара до СПП") == money("4490663.54")
    assert column("Средний чек продажи до СПП") == money("6141.20")
    assert column("Вся стоимость реализованного товара после СПП") == money("2623733.83")
    assert column("Сумма СПП") == money("1866929.71")
    assert column("К перечислению за товар") == money("2616897.01")
    assert column("Плановая комиссия") == money("1873766.53")
    assert column("Плановая комиссия на единицу") == money("2563.29")
    assert column("Фактическая комиссия") == money("6836.82")
    assert column("% комиссии ВБ до СПП") == Decimal("0.42")
    assert column("Стоимость логистики") == money("169595.18")
    assert column("в т.ч.  Стоимость логистики до клиента") == money("158221")
    assert column("в т.ч. Логистика другое") == money("1073.18")
    assert column("Итого стоимость всех услуг ВБ от реализации до СПП") == money("2397890.71")
    assert column("Итого стоимость всех услуг ВБ от реализации после СПП") == money("530961")
    assert column("Итого к оплате") == money("2092772.83")
    assert column("Налог") == money("314848.05")
    assert column("Итого к оплате за вычетом налога") == money("1777924.78")
    assert column("Себестоимость реализованного товара") == money("1115506")
    assert column("Себестоимость остатка до клиента на последнюю неделю") == money("889658")
    assert column("Операционная прибыль") == money("662418.78")
    assert column("Операционная прибыль на единицу") == money("906.18")
    assert column("% прибыли от суммы реализации до СПП") == Decimal("0.15")
    assert column("Реклама баланс + счет") == money("281428")
    assert column("Инф: Реклама из ВБ Продвижение (Баланс + счет)") == money("260009")
    assert (column("Доля размера в продажах"), column("% от всей суммы реализации")) == (Decimal(1), Decimal(1))


def test_deductions_without_an_article_make_their_own_row_and_a_loss_shows_no_profit_rate() -> None:
    rows = article_rows(
        [
            totals(
                doc_type_name="Продажа", quantity=1, gross=money(1000), retail_amount=money(800), for_pay=money(700)
            ),
            totals(
                nm_id=0,
                seller_oper_name="Удержание",
                bonus_type_name='Возврат неиспользованного остатка аванса за услугу "Баллы за отзывы"',
                deduction=money(-500),
            ),
            totals(
                nm_id=0,
                seller_oper_name="Удержание",
                bonus_type_name="Оказание услуг «WB Продвижение», документ №1",
                deduction=money(900),
            ),
        ],
        cost_of=lambda nm_id: money(950),
        stock_of=lambda nm_id, size: Stock(),
        ads_of=lambda nm_id: AdSpend(),
        tax_rate=Decimal("0.08"),
    )

    assert [row.nm_id for row in rows] == [DRILL, 0]
    sale, rest = rows
    # Убыток: к оплате 700 без налога 64 меньше себестоимости 950 — доли прибыли нули, а не минус.
    assert sale.operating_profit == money("-314") and sale.profit_rate_gross == 0
    assert (rest.reviews, rest.ads.promotion_info) == (money(-500), money(900))
    assert COLUMN["Артикул ВБ"](rest) == "(пусто)"


def test_stock_rows_use_the_week_price_or_the_last_known_one() -> None:
    rows = article_rows(
        [
            totals(doc_type_name="Продажа", quantity=2, gross=money(7000), retail_amount=money(5000)),
            totals(nm_id=SAW, doc_type_name="Логистика", delivery_service=money(100)),
        ],
        cost_of=lambda nm_id: money(1000) if nm_id == DRILL else None,
        stock_of=lambda nm_id, size: Stock(in_warehouse=10 if nm_id == DRILL else 3),
        ads_of=lambda nm_id: AdSpend(),
        tax_rate=Decimal("0.12"),
    )

    # Меньший остаток первым, как в старом листе.
    saw, drill = stock_rows(rows, last_price_of={SAW: money(1200)}.get)

    assert (drill.quantity, drill.stock_cost, drill.stock_price) == (10, money(10000), money(35000))
    assert drill.daily_sales == money(1000) and drill.turnover_days == 35 and drill.category == "30-60"
    assert (saw.stock_price, saw.turnover_days, saw.category) == (money(3600), None, "Нет продаж!")


def test_remains_fold_into_stock_per_size() -> None:
    remain = lambda size, warehouse, quantity: WarehouseRemain(  # noqa: E731
        f"20{size}", str(DRILL), size, "KARBI", warehouse, quantity
    )
    rows = [
        remain("S", "Коледино", 5),
        remain("S", "Тула", 2),
        remain("S", "В пути до получателей", 4),
        remain("S", "Всего находится на складах", 7),
        remain("M", "Коледино", 9),
        remain("M", "В пути возвраты на склад WB", 1),
        WarehouseRemain("3", "not-a-number", "", "x", "Коледино", 1),
    ]

    stocks = fold_remains(rows)

    # Итог отчёта не прибавляется к складам; «всего» — склады плюс путь туда и обратно.
    assert stocks[(DRILL, "S")] == Stock(in_warehouse=7, to_client=4, from_client=0, total=11)
    assert stocks[(DRILL, "M")] == Stock(in_warehouse=9, to_client=0, from_client=1, total=10)
    assert (SAW, "") not in stocks


def test_the_closed_week_is_snapshotted_from_monday_morning() -> None:
    sunday = date(2026, 10, 4)
    assert last_closed_week_end(datetime(2026, 10, 5, 2, 59, tzinfo=MOSCOW), MOSCOW) is None
    assert last_closed_week_end(datetime(2026, 10, 5, 3, 0, tzinfo=MOSCOW), MOSCOW) == sunday
    assert last_closed_week_end(datetime(2026, 10, 8, 12, 0, tzinfo=MOSCOW), MOSCOW) == sunday
    # UTC-полночь понедельника — ещё воскресенье 23:00 по Москве? Нет: 03:00 МСК, снимок пора снимать.
    assert last_closed_week_end(datetime(2026, 10, 5, 0, 0, tzinfo=UTC), MOSCOW) == sunday


def test_a_row_with_only_stock_and_no_sales_is_still_an_article_row() -> None:
    [row] = article_rows(
        [totals(doc_type_name="Логистика", delivery_amount=1, delivery_service=money(50))],
        cost_of=lambda nm_id: None,
        stock_of=lambda nm_id, size: Stock(in_warehouse=4),
        ads_of=lambda nm_id: AdSpend(),
        tax_rate=Decimal("0"),
    )
    assert (row.sales, row.realized, row.stock_in_warehouse, row.cost) == (0, 0, 4, 0)
    assert (
        COLUMN["Логистика на единицу товара"](row) == 0
        and COLUMN["Средняя себестоимость на единицу товара"](row) is None
    )


def test_a_stocked_article_without_movement_and_unknown_campaign_spend_get_their_rows() -> None:
    rows = article_rows(
        [totals(doc_type_name="Продажа", quantity=1, gross=money(1000), retail_amount=money(800), for_pay=money(700))],
        cost_of=lambda nm_id: money(400),
        stock_of=lambda nm_id, size: Stock(in_warehouse=30, total=30) if nm_id == SAW else Stock(),
        ads_of=lambda nm_id: AdSpend(),
        tax_rate=Decimal("0.08"),
        stocked=[(SAW, "L"), (DRILL, "0"), (999, "M")],
        unallocated_ads=AdSpend(balance=money(7)),
    )

    # Пила не продавалась, но лежит на складе — строка есть; артикул без остатка — нет.
    assert [(row.nm_id, row.tech_size) for row in rows] == [(DRILL, ""), (SAW, "L"), (0, "")]
    saw = rows[1]
    assert (saw.sales, saw.stock_in_warehouse, saw.unit_cost) == (0, 30, money(400))
    stocks = stock_rows(rows, last_price_of=lambda nm_id: money(3600))
    assert [(row.nm_id, row.quantity, row.stock_cost) for row in stocks] == [(SAW, 30, money(12000))]
    # Списания неизвестных кампаний — в строке «(пусто)», даже если удержаний по документам не было.
    assert rows[-1].ads.balance == money(7)
